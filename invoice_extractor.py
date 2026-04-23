from __future__ import annotations

import base64
import io
import json
import os
from dataclasses import dataclass
from typing import Callable

import pandas as pd
from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    OpenAI,
    RateLimitError,
)
from openpyxl.utils import get_column_letter
from pdf2image import convert_from_bytes
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError, PDFSyntaxError
from PIL import Image
from pydantic import BaseModel, ConfigDict, ValidationError


STATUS_CALLBACK = Callable[[str, str, str], None]


INVOICE_SCHEMA = {
    "name": "invoice_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "invoice_date": {"type": "string"},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": {"type": "string"},
                        "specification": {"type": "string"},
                        "unit": {"type": "string"},
                        "quantity": {"type": "string"},
                        "unit_price": {"type": "string"},
                        "amount": {"type": "string"},
                        "tax_rate": {"type": "string"},
                        "tax_amount": {"type": "string"},
                    },
                    "required": [
                        "name",
                        "specification",
                        "unit",
                        "quantity",
                        "unit_price",
                        "amount",
                        "tax_rate",
                        "tax_amount",
                    ],
                },
            },
            "total_amount_with_tax": {"type": "string"},
            "total_tax": {"type": "string"},
        },
        "required": ["invoice_date", "items", "total_amount_with_tax", "total_tax"],
    },
}


SYSTEM_PROMPT = """
你是一名擅长读取中国增值税发票和发票清单的结构化提取助手。

当前请求中的多张图片全部来自同一个 PDF 文件，可能包含:
1. 发票首页
2. 商品清单续页
3. 同一商品因为版式问题而被拆成多行显示

请严格遵守以下规则:
1. 只返回 JSON，不要返回 Markdown、解释、备注或代码块。
2. 一定要输出以下字段:
   - invoice_date
   - items
   - total_amount_with_tax
   - total_tax
3. items 里的每个元素必须包含:
   - name
   - specification
   - unit
   - quantity
   - unit_price
   - amount
   - tax_rate
   - tax_amount
4. 如果同一条商品明细因为视觉换行被拆成两行或多行，请合并成一个逻辑商品，不要拆成多个 items。
5. 商品明细要按票面顺序输出；不要把“合计”“价税合计”“大写金额”“备注”“密码区”“购买方/销售方信息”识别成商品行。
6. 如果存在清单页，请继续把清单页中的商品明细追加到 items。
7. 字段缺失时必须返回空字符串，不要返回 null。
8. 金额、数量、税率等尽量保持票面原始表达，不要添加额外单位或说明。
9. invoice_date 优先输出 YYYY-MM-DD；如果无法标准化，则保留票面原文。
10. total_amount_with_tax 对应“价税合计/小写金额”，total_tax 对应“合计税额”。
"""


class InvoiceExtractorError(RuntimeError):
    """Raised when invoice extraction fails."""


class InvoiceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    specification: str
    unit: str
    quantity: str
    unit_price: str
    amount: str
    tax_rate: str
    tax_amount: str


class InvoicePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_date: str
    items: list[InvoiceItem]
    total_amount_with_tax: str
    total_tax: str


@dataclass(slots=True)
class ParsedInvoice:
    source_file: str
    page_count: int
    invoice_date: str
    items: list[InvoiceItem]
    total_amount_with_tax: str
    total_tax: str

    @classmethod
    def from_payload(
        cls,
        source_file: str,
        page_count: int,
        payload: InvoicePayload,
    ) -> "ParsedInvoice":
        return cls(
            source_file=source_file,
            page_count=page_count,
            invoice_date=payload.invoice_date.strip(),
            items=payload.items,
            total_amount_with_tax=payload.total_amount_with_tax.strip(),
            total_tax=payload.total_tax.strip(),
        )

    def to_detail_rows(self) -> list[dict[str, str | int]]:
        rows: list[dict[str, str | int]] = []
        for index, item in enumerate(self.items, start=1):
            rows.append(
                {
                    "source_file": self.source_file,
                    "invoice_date": self.invoice_date,
                    "item_index": index,
                    "name": item.name.strip(),
                    "specification": item.specification.strip(),
                    "unit": item.unit.strip(),
                    "quantity": item.quantity.strip(),
                    "unit_price": item.unit_price.strip(),
                    "amount": item.amount.strip(),
                    "tax_rate": item.tax_rate.strip(),
                    "tax_amount": item.tax_amount.strip(),
                    "total_amount_with_tax": self.total_amount_with_tax,
                    "total_tax": self.total_tax,
                }
            )
        return rows

    def to_summary_row(self) -> dict[str, str | int]:
        return {
            "source_file": self.source_file,
            "page_count": self.page_count,
            "invoice_date": self.invoice_date,
            "item_count": len(self.items),
            "total_amount_with_tax": self.total_amount_with_tax,
            "total_tax": self.total_tax,
        }


def mask_secret(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return "未配置"
    if len(cleaned) <= 8:
        return "已配置"
    return f"{cleaned[:4]}...{cleaned[-4:]}"


def build_excel_bytes(detail_rows: list[dict], summary_rows: list[dict]) -> bytes:
    detail_df = pd.DataFrame(detail_rows)
    summary_df = pd.DataFrame(summary_rows)

    if detail_df.empty:
        detail_df = pd.DataFrame(
            columns=[
                "source_file",
                "invoice_date",
                "item_index",
                "name",
                "specification",
                "unit",
                "quantity",
                "unit_price",
                "amount",
                "tax_rate",
                "tax_amount",
                "total_amount_with_tax",
                "total_tax",
            ]
        )

    if summary_df.empty:
        summary_df = pd.DataFrame(
            columns=[
                "source_file",
                "page_count",
                "invoice_date",
                "item_count",
                "total_amount_with_tax",
                "total_tax",
            ]
        )

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        detail_df.to_excel(writer, sheet_name="明细汇总", index=False)
        summary_df.to_excel(writer, sheet_name="发票汇总", index=False)

        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            for column in worksheet.columns:
                values = [len(str(cell.value)) for cell in column if cell.value is not None]
                column_width = min(max(values, default=10) + 2, 40)
                worksheet.column_dimensions[get_column_letter(column[0].column)].width = column_width

    buffer.seek(0)
    return buffer.read()


class InvoiceExtractor:
    def __init__(self, api_key: str, base_url: str | None = None, model: str = "gpt-4o") -> None:
        if not api_key.strip():
            raise InvoiceExtractorError("OPENAI_API_KEY 未配置，无法调用模型。")

        self.client = OpenAI(
            api_key=api_key.strip(),
            base_url=base_url.strip() if base_url else None,
            max_retries=2,
            timeout=180.0,
        )
        self.model = model
        self.poppler_path = os.getenv("POPPLER_PATH") or None

    def pdf_to_images(
        self,
        pdf_bytes: bytes,
        status_callback: STATUS_CALLBACK | None = None,
    ) -> list[Image.Image]:
        self._notify(status_callback, "pdf", "正在把 PDF 转成高清图片", "running")
        try:
            return convert_from_bytes(
                pdf_bytes,
                dpi=240,
                fmt="png",
                thread_count=2,
                poppler_path=self.poppler_path,
            )
        except PDFInfoNotInstalledError as exc:
            raise InvoiceExtractorError(
                "PDF 转图片失败。当前环境缺少 Poppler，请在本机安装 Poppler 或直接使用 Docker 版本。"
            ) from exc
        except (PDFPageCountError, PDFSyntaxError, ValueError) as exc:
            raise InvoiceExtractorError(f"PDF 文件无法解析：{exc}") from exc
        except Exception as exc:
            raise InvoiceExtractorError(f"PDF 转图片失败：{exc}") from exc

    def extract_from_images(
        self,
        images: list[Image.Image],
        source_file: str,
        status_callback: STATUS_CALLBACK | None = None,
    ) -> ParsedInvoice:
        if not images:
            raise InvoiceExtractorError("PDF 未生成任何页面图片。")

        self._notify(status_callback, "preview", f"已生成 {len(images)} 页图片，准备预览", "running")

        user_content: list[dict] = [
            {
                "type": "text",
                "text": (
                    "请从以下中国发票图片中提取结构化数据。\n"
                    f"文件名: {source_file}\n"
                    f"页数: {len(images)}\n"
                    "注意识别跨行商品明细，保持商品顺序，并严格按 JSON Schema 返回。"
                ),
            }
        ]

        for index, image in enumerate(images, start=1):
            image_url = self._image_to_data_url(image)
            user_content.append(
                {
                    "type": "text",
                    "text": f"第 {index} 页图片如下。",
                }
            )
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": image_url,
                        "detail": "high",
                    },
                }
            )

        try:
            self._notify(status_callback, "openai", "正在向 OpenAI 发送图片并等待识别结果", "running")
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=4000,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_schema", "json_schema": INVOICE_SCHEMA},
            )
            payload = self._parse_response(response)
        except BadRequestError as exc:
            payload = self._fallback_json_mode(images, source_file, status_callback, exc)
        except (AuthenticationError, APIConnectionError, APITimeoutError, RateLimitError, APIError) as exc:
            raise InvoiceExtractorError(f"OpenAI 调用失败：{exc}") from exc

        self._notify(status_callback, "validate", "模型已返回结果，正在校验 JSON 结构", "running")
        try:
            validated = InvoicePayload.model_validate(payload)
        except ValidationError as exc:
            raise InvoiceExtractorError(f"模型返回的 JSON 结构不符合预期：{exc}") from exc

        normalized = self._normalize_payload(validated)
        self._notify(status_callback, "done", "当前文件解析完成，准备汇总到结果表", "success")
        return ParsedInvoice.from_payload(
            source_file=source_file,
            page_count=len(images),
            payload=normalized,
        )

    def _fallback_json_mode(
        self,
        images: list[Image.Image],
        source_file: str,
        status_callback: STATUS_CALLBACK | None,
        original_error: Exception,
    ) -> dict:
        self._notify(
            status_callback,
            "fallback",
            "结构化输出模式不可用，正在切换为 JSON 模式重试",
            "warning",
        )
        user_content: list[dict] = [
            {
                "type": "text",
                "text": (
                    "请只返回一个 JSON 对象，不要包含任何说明。\n"
                    f"文件名: {source_file}\n"
                    "字段必须包括 invoice_date、items、total_amount_with_tax、total_tax。"
                ),
            }
        ]
        for image in images:
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": self._image_to_data_url(image),
                        "detail": "high",
                    },
                }
            )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                temperature=0,
                max_tokens=4000,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
            )
            return self._parse_response(response)
        except (AuthenticationError, APIConnectionError, APITimeoutError, RateLimitError, APIError, BadRequestError) as exc:
            raise InvoiceExtractorError(
                f"OpenAI 调用失败，结构化输出重试也未成功。原始错误: {original_error}; 重试错误: {exc}"
            ) from exc

    @staticmethod
    def _notify(
        callback: STATUS_CALLBACK | None,
        stage: str,
        message: str,
        level: str,
    ) -> None:
        if callback:
            callback(stage, message, level)

    @staticmethod
    def _image_to_data_url(image: Image.Image) -> str:
        buffer = io.BytesIO()
        converted = image.convert("RGB")
        converted.save(buffer, format="PNG", optimize=True)
        encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:image/png;base64,{encoded}"

    @staticmethod
    def _parse_response(response) -> dict:
        message = response.choices[0].message
        refusal = getattr(message, "refusal", None)
        if refusal:
            raise InvoiceExtractorError(f"模型拒绝处理该请求：{refusal}")

        content = message.content
        if not content:
            raise InvoiceExtractorError("模型未返回任何内容。")

        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise InvoiceExtractorError(f"模型返回内容不是有效 JSON：{exc}") from exc

    @staticmethod
    def _normalize_payload(payload: InvoicePayload) -> InvoicePayload:
        cleaned_items = []
        for item in payload.items:
            cleaned_items.append(
                InvoiceItem(
                    name=item.name.strip(),
                    specification=item.specification.strip(),
                    unit=item.unit.strip(),
                    quantity=item.quantity.strip(),
                    unit_price=item.unit_price.strip(),
                    amount=item.amount.strip(),
                    tax_rate=item.tax_rate.strip(),
                    tax_amount=item.tax_amount.strip(),
                )
            )

        return InvoicePayload(
            invoice_date=payload.invoice_date.strip(),
            items=cleaned_items,
            total_amount_with_tax=payload.total_amount_with_tax.strip(),
            total_tax=payload.total_tax.strip(),
        )
