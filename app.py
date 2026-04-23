from __future__ import annotations

import os
from datetime import datetime

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from invoice_extractor import InvoiceExtractor, InvoiceExtractorError, build_excel_bytes, mask_secret


load_dotenv()

st.set_page_config(
    page_title="中国发票 PDF 数据提取工具",
    layout="wide",
    initial_sidebar_state="expanded",
)


MODEL_NAME = "gpt-4o"


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@500;700&family=Noto+Sans+SC:wght@400;500;700&display=swap');

        html, body, [class*="css"] {
            font-family: "Noto Sans SC", "Microsoft YaHei", sans-serif;
        }

        .stApp {
            background:
                radial-gradient(circle at top right, rgba(245, 158, 11, 0.14), transparent 28%),
                radial-gradient(circle at bottom left, rgba(14, 116, 144, 0.10), transparent 32%),
                linear-gradient(180deg, #fffaf2 0%, #fff7eb 100%);
        }

        h1, h2, h3 {
            font-family: "DM Sans", "Noto Sans SC", sans-serif;
            letter-spacing: -0.02em;
        }

        .hero-card, .step-card, .status-banner {
            border: 1px solid rgba(180, 83, 9, 0.12);
            background: rgba(255, 255, 255, 0.82);
            backdrop-filter: blur(12px);
            border-radius: 22px;
            box-shadow: 0 18px 48px rgba(15, 23, 42, 0.06);
        }

        .hero-card {
            padding: 28px 30px;
            margin-bottom: 18px;
        }

        .hero-kicker {
            color: #b45309;
            font-size: 0.92rem;
            font-weight: 700;
            margin-bottom: 8px;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }

        .hero-title {
            margin: 0 0 10px 0;
            color: #111827;
            font-size: 2.2rem;
            line-height: 1.1;
        }

        .hero-text {
            color: #374151;
            font-size: 1rem;
            line-height: 1.7;
            margin: 0;
        }

        .step-card {
            padding: 20px 18px;
            min-height: 150px;
        }

        .step-index {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: 34px;
            height: 34px;
            border-radius: 999px;
            background: #f59e0b;
            color: #ffffff;
            font-weight: 700;
            margin-bottom: 14px;
        }

        .step-title {
            margin: 0 0 10px 0;
            color: #111827;
            font-size: 1.05rem;
            font-weight: 700;
        }

        .step-text {
            margin: 0;
            color: #4b5563;
            line-height: 1.65;
            font-size: 0.96rem;
        }

        .status-banner {
            padding: 20px 22px;
            margin-bottom: 14px;
        }

        .status-label {
            font-size: 0.82rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: #92400e;
            margin-bottom: 8px;
        }

        .status-title {
            margin: 0 0 8px 0;
            font-size: 1.15rem;
            color: #111827;
        }

        .status-text {
            margin: 0;
            line-height: 1.7;
            color: #4b5563;
        }

        .log-shell {
            display: grid;
            gap: 10px;
            margin-top: 8px;
        }

        .log-entry {
            border-radius: 18px;
            padding: 14px 16px;
            background: rgba(255, 255, 255, 0.82);
            border: 1px solid rgba(148, 163, 184, 0.18);
        }

        .log-entry.running {
            border-left: 5px solid #d97706;
        }

        .log-entry.success {
            border-left: 5px solid #0f766e;
        }

        .log-entry.warning {
            border-left: 5px solid #b45309;
        }

        .log-entry.error {
            border-left: 5px solid #b91c1c;
        }

        .log-time {
            color: #6b7280;
            font-size: 0.82rem;
            margin-bottom: 5px;
        }

        .log-stage {
            font-weight: 700;
            color: #111827;
            margin-bottom: 3px;
        }

        .log-detail {
            color: #4b5563;
            line-height: 1.65;
            font-size: 0.94rem;
        }

        .login-card {
            border: 1px solid rgba(180, 83, 9, 0.14);
            background: rgba(255, 255, 255, 0.86);
            border-radius: 24px;
            padding: 28px;
            box-shadow: 0 18px 44px rgba(15, 23, 42, 0.08);
        }

        div[data-testid="stDataFrame"] {
            border-radius: 18px;
            overflow: hidden;
        }

        div[data-testid="stFileUploader"] section {
            border-radius: 18px;
            border: 1px dashed rgba(180, 83, 9, 0.35);
            background: rgba(255, 251, 235, 0.88);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def init_session_state() -> None:
    defaults = {
        "authenticated": False,
        "auth_error": "",
        "extraction_result": None,
        "upload_signature": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def render_sidebar() -> None:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "").strip()
    app_password = os.getenv("APP_PASSWORD", "").strip()

    with st.sidebar:
        st.markdown("## 配置状态")
        st.caption("敏感信息已做掩码处理。")
        st.text_input("模型", value=MODEL_NAME, disabled=True)
        st.text_input("OPENAI_BASE_URL", value=base_url or "官方默认地址", disabled=True)
        st.text_input("OPENAI_API_KEY", value=mask_secret(api_key), disabled=True)
        st.text_input("APP_PASSWORD", value="已配置" if app_password else "未配置", disabled=True)
        st.markdown("---")
        st.markdown("### 处理流程")
        st.caption("登录验证 -> 上传 PDF -> 转图片 -> GPT-4o 识别 -> 校验 JSON -> 汇总 Excel")
        st.markdown("---")
        if st.session_state.get("authenticated") and st.button("退出登录", use_container_width=True):
            st.session_state.authenticated = False
            st.session_state.auth_error = ""
            st.rerun()


def render_hero() -> None:
    st.markdown(
        """
        <div class="hero-card">
            <div class="hero-kicker">Streamlit + GPT-4o Vision</div>
            <h1 class="hero-title">中国发票 PDF 数据提取工具</h1>
            <p class="hero-text">
                上传一个或多个中国发票 PDF，系统会先把每一页转换成高清图片，再调用 OpenAI 视觉模型识别商品明细、
                开票日期与金额信息，最后汇总导出为 Excel。
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    columns = st.columns(3)
    steps = [
        ("01", "上传发票 PDF", "支持一次上传多个 PDF。每个文件会单独转图并按顺序解析。"),
        ("02", "查看实时状态", "页面会持续显示当前步骤、文件名、图片预览与处理日志，避免等待时误以为卡住。"),
        ("03", "下载汇总 Excel", "所有商品明细会自动合并为一张总表，并附带每张发票的汇总页。"),
    ]
    for column, (index, title, text) in zip(columns, steps):
        with column:
            st.markdown(
                f"""
                <div class="step-card">
                    <div class="step-index">{index}</div>
                    <div class="step-title">{title}</div>
                    <p class="step-text">{text}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )


def render_login() -> None:
    _, center, _ = st.columns([1, 1.2, 1])
    with center:
        st.markdown('<div class="login-card">', unsafe_allow_html=True)
        st.markdown("### 输入访问密码")
        st.caption("只有密码验证通过后，才会显示上传与提取界面。")

        with st.form("login_form", clear_on_submit=False):
            password_input = st.text_input("访问密码", type="password", placeholder="请输入 APP_PASSWORD")
            submitted = st.form_submit_button("进入系统", use_container_width=True, type="primary")

        if submitted:
            expected_password = os.getenv("APP_PASSWORD", "").strip()
            if password_input == expected_password:
                st.session_state.authenticated = True
                st.session_state.auth_error = ""
                st.rerun()
            st.session_state.auth_error = "密码错误，请重新输入。"

        if st.session_state.auth_error:
            st.error(st.session_state.auth_error)
        st.markdown("</div>", unsafe_allow_html=True)


def ensure_password_configured() -> None:
    if not os.getenv("APP_PASSWORD", "").strip():
        st.error("服务器未配置 APP_PASSWORD，应用已停止。请先在环境变量或 .env 中设置访问密码。")
        st.stop()


def ensure_api_key_configured() -> None:
    if not os.getenv("OPENAI_API_KEY", "").strip():
        st.error("未检测到 OPENAI_API_KEY，请先在环境变量或 .env 文件中配置后再使用。")
        st.stop()


def reset_results_if_uploads_changed(uploaded_files) -> None:
    current_signature = tuple((file.name, file.size) for file in uploaded_files) if uploaded_files else None
    if st.session_state.upload_signature != current_signature:
        st.session_state.upload_signature = current_signature
        st.session_state.extraction_result = None


def render_runtime_banner(container, title: str, detail: str) -> None:
    container.markdown(
        f"""
        <div class="status-banner">
            <div class="status-label">当前程序状态</div>
            <h3 class="status-title">{title}</h3>
            <p class="status-text">{detail}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_runtime_logs(container, entries: list[dict[str, str]]) -> None:
    blocks = []
    for entry in reversed(entries[-8:]):
        blocks.append(
            f"""
            <div class="log-entry {entry['level']}">
                <div class="log-time">{entry['time']}</div>
                <div class="log-stage">{entry['stage']}</div>
                <div class="log-detail">{entry['detail']}</div>
            </div>
            """
        )

    container.markdown(f"<div class='log-shell'>{''.join(blocks)}</div>", unsafe_allow_html=True)


def process_files(uploaded_files) -> None:
    extractor = InvoiceExtractor(
        api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        base_url=os.getenv("OPENAI_BASE_URL", "").strip() or None,
        model=MODEL_NAME,
    )

    detail_rows: list[dict] = []
    summary_rows: list[dict] = []
    errors: list[str] = []
    runtime_entries: list[dict[str, str]] = []
    total_files = len(uploaded_files)
    total_steps = total_files * 6 + 1
    completed_steps = 0

    progress_bar = st.progress(0, text="任务排队中")
    banner_placeholder = st.empty()
    log_placeholder = st.empty()
    preview_placeholder = st.empty()

    def push_status(stage: str, detail: str, level: str = "running") -> None:
        runtime_entries.append(
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "stage": stage,
                "detail": detail,
                "level": level,
            }
        )
        render_runtime_banner(banner_placeholder, stage, detail)
        render_runtime_logs(log_placeholder, runtime_entries)

    def advance_progress(text: str) -> None:
        nonlocal completed_steps
        completed_steps += 1
        progress_bar.progress(min(completed_steps / total_steps, 1.0), text=text)

    push_status("准备开始", f"已收到 {total_files} 个 PDF，准备逐个处理。")

    for file_index, uploaded_file in enumerate(uploaded_files, start=1):
        file_name = uploaded_file.name
        pdf_bytes = uploaded_file.getvalue()
        file_steps_completed = 0

        push_status("读取文件", f"正在读取第 {file_index}/{total_files} 个文件：{file_name}")
        advance_progress(f"读取文件 {file_index}/{total_files}")
        file_steps_completed += 1

        try:
            images = extractor.pdf_to_images(pdf_bytes, status_callback=lambda _, message, level: push_status("PDF 转图片", message, level))
            advance_progress(f"PDF 转图片完成 {file_index}/{total_files}")
            file_steps_completed += 1

            push_status("图片预览", f"{file_name} 已生成 {len(images)} 页预览图，用户可以实时看到当前页。")
            with preview_placeholder.container():
                st.markdown("### 当前文件预览")
                st.caption(f"文件：{file_name}")
                captions = [f"{file_name} - 第 {page_index} 页" for page_index in range(1, len(images) + 1)]
                st.image(images, caption=captions, use_container_width=True)
            advance_progress(f"图片预览完成 {file_index}/{total_files}")
            file_steps_completed += 1

            parsed = extractor.extract_from_images(
                images,
                file_name,
                status_callback=lambda stage, message, level: push_status(resolve_stage_name(stage), message, level),
            )
            advance_progress(f"OpenAI 识别完成 {file_index}/{total_files}")
            file_steps_completed += 1
            advance_progress(f"JSON 校验完成 {file_index}/{total_files}")
            file_steps_completed += 1

            detail_rows.extend(parsed.to_detail_rows())
            summary_rows.append(parsed.to_summary_row())
            push_status(
                "结果汇总",
                f"{file_name} 提取完成，共识别 {len(parsed.items)} 条商品明细，已汇总到结果表。",
                "success",
            )
            advance_progress(f"结果汇总完成 {file_index}/{total_files}")
            file_steps_completed += 1
        except InvoiceExtractorError as exc:
            errors.append(f"{file_name}: {exc}")
            push_status("处理失败", f"{file_name} 处理失败：{exc}", "error")
            remaining_steps = 6 - file_steps_completed
            for skipped_step in range(remaining_steps):
                advance_progress(f"{file_name} 异常补齐进度 {skipped_step + 1}/{remaining_steps}")
        except Exception as exc:
            errors.append(f"{file_name}: 未预期错误 - {exc}")
            push_status("处理失败", f"{file_name} 处理时出现未预期错误：{exc}", "error")
            remaining_steps = 6 - file_steps_completed
            for skipped_step in range(remaining_steps):
                advance_progress(f"{file_name} 异常补齐进度 {skipped_step + 1}/{remaining_steps}")

    push_status("生成 Excel", "所有文件已处理完毕，正在整理 DataFrame 并生成可下载的 Excel 文件。")
    try:
        excel_bytes = build_excel_bytes(detail_rows, summary_rows)
    except Exception as exc:
        push_status("生成失败", f"Excel 生成失败：{exc}", "error")
        st.error(f"Excel 生成失败：{exc}")
        return
    advance_progress("Excel 已生成")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    st.session_state.extraction_result = {
        "detail_rows": detail_rows,
        "summary_rows": summary_rows,
        "errors": errors,
        "excel_bytes": excel_bytes,
        "filename": f"发票提取汇总_{timestamp}.xlsx",
    }
    push_status("全部完成", "本次任务处理结束，可以预览表格并下载 Excel。", "success")


def resolve_stage_name(stage: str) -> str:
    mapping = {
        "pdf": "PDF 转图片",
        "preview": "图片预览",
        "openai": "调用 OpenAI",
        "validate": "结果校验",
        "fallback": "降级重试",
        "done": "单文件完成",
    }
    return mapping.get(stage, "处理中")


def render_results() -> None:
    result = st.session_state.extraction_result
    if not result:
        return

    detail_df = pd.DataFrame(result["detail_rows"])
    summary_df = pd.DataFrame(result["summary_rows"])

    st.markdown("## 提取结果")
    metric_columns = st.columns(3)
    metric_columns[0].metric("成功发票数", f"{len(summary_df)}")
    metric_columns[1].metric("商品明细行数", f"{len(detail_df)}")
    metric_columns[2].metric("失败文件数", f"{len(result['errors'])}")

    if result["errors"]:
        st.warning("以下文件未能成功提取：\n\n" + "\n".join(f"- {error}" for error in result["errors"]))

    if not detail_df.empty:
        st.markdown("### 明细预览")
        st.dataframe(detail_df, use_container_width=True, hide_index=True)
    else:
        st.info("本次成功提取的发票没有识别到商品明细，仍可下载汇总 Excel 查看发票级结果。")

    if not summary_df.empty:
        st.markdown("### 发票汇总预览")
        st.dataframe(summary_df, use_container_width=True, hide_index=True)

    st.download_button(
        "下载 Excel",
        data=result["excel_bytes"],
        file_name=result["filename"],
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
        type="primary",
    )


def main() -> None:
    inject_styles()
    init_session_state()
    render_sidebar()
    ensure_password_configured()

    if not st.session_state.authenticated:
        render_login()
        return

    ensure_api_key_configured()
    render_hero()

    st.markdown("## 上传 PDF")
    st.caption("建议每个 PDF 对应一张发票或一套发票清单。系统会在处理过程中持续展示当前状态和预览。")
    uploaded_files = st.file_uploader(
        "上传一个或多个 PDF 文件",
        type=["pdf"],
        accept_multiple_files=True,
        help="支持批量上传，处理顺序与上传顺序一致。",
    )
    reset_results_if_uploads_changed(uploaded_files)

    if st.button("开始提取", type="primary", use_container_width=True, disabled=not uploaded_files):
        if not uploaded_files:
            st.warning("请先上传至少一个 PDF 文件。")
        else:
            process_files(uploaded_files)

    render_results()


if __name__ == "__main__":
    main()
