# 中国发票 PDF 提取工具

一个基于 Streamlit 的中国发票 PDF 数据提取工具。用户上传一个或多个 PDF 后，系统会先用 `pdf2image` 把每一页转换为图片，再调用 OpenAI `gpt-4o` 进行视觉识别，最后把所有商品明细合并导出为 Excel。

仓库地址：

- `https://github.com/zhizinan1997/invoice-pdf-extractor`

## 功能特性

- 密码登录保护，未验证前不显示上传界面
- 支持多文件上传与逐个处理
- PDF 每页转高清图片后交给 GPT-4o 识别
- 针对中国发票明细表做了专门提示词优化，重点处理跨行商品明细
- 处理过程中实时展示当前程序状态、文件名、阶段日志和图片预览
- 自动生成 Excel，包含 `明细汇总` 和 `发票汇总` 两个工作表
- 支持 Docker 部署，镜像内已安装 `poppler-utils`

## 环境变量

复制 `.env.example` 为 `.env`，并填入实际值：

```env
OPENAI_API_KEY=sk-xxxx...
OPENAI_BASE_URL=https://api.openai.com/v1
APP_PASSWORD=your-password
```

说明：

- `OPENAI_API_KEY`：必填
- `OPENAI_BASE_URL`：可选，使用代理或兼容网关时填写
- `APP_PASSWORD`：必填，控制网页访问
- `POPPLER_PATH`：可选，仅在本机 Windows 没有把 Poppler 放进 PATH 时使用

## 本地运行

1. 安装 Python 3.11 或更高版本
2. 安装依赖

```bash
pip install -r requirements.txt
```

3. 启动应用

```bash
streamlit run app.py
```

4. 浏览器访问 `http://localhost:8501`

### Windows 本地运行提示

如果你不是通过 Docker 运行，并且遇到 `PDF 转图片失败`，通常是因为系统没有安装 Poppler。可以：

1. 安装 Poppler 并加入系统 PATH
2. 或设置环境变量 `POPPLER_PATH` 指向 Poppler 的 `bin` 目录

## Docker 运行

### 方式一：本地构建后运行

构建镜像：

```bash
docker build -t invoice-pdf-extractor .
```

使用 `.env` 启动容器：

```bash
docker run -d \
  --name invoice-pdf-extractor \
  --restart unless-stopped \
  -p 8501:8501 \
  --env-file .env \
  invoice-pdf-extractor
```

查看容器日志：

```bash
docker logs -f invoice-pdf-extractor
```

停止并删除容器：

```bash
docker stop invoice-pdf-extractor
docker rm invoice-pdf-extractor
```

### 方式二：从 GHCR 直接运行

如果你已经发布了 GitHub Release，并且镜像已推送到 GHCR，可以直接拉取运行。

先登录 GHCR：

```bash
docker login ghcr.io -u zhizinan1997
```

说明：

- 如果镜像是私有的，需要输入一个带 `read:packages` 权限的 GitHub Personal Access Token 作为密码

拉取镜像：

```bash
docker pull ghcr.io/zhizinan1997/invoice-pdf-extractor:latest
```

运行容器：

```bash
docker run -d \
  --name invoice-pdf-extractor \
  --restart unless-stopped \
  -p 8501:8501 \
  --env-file .env \
  ghcr.io/zhizinan1997/invoice-pdf-extractor:latest
```

## GitHub Actions 与 GHCR

- `CI` 工作流会在推送到 `main` 或创建 Pull Request 时执行依赖安装、语法检查和 Docker 构建。
- `Release Image` 工作流会在 GitHub 上发布 Release 后自动把镜像推送到 `ghcr.io/zhizinan1997/invoice-pdf-extractor`。
- 建议使用语义化版本标签，例如 `v1.0.0`。当发布这个版本的 GitHub Release 时，镜像会自动生成类似标签：
  - `ghcr.io/zhizinan1997/invoice-pdf-extractor:v1.0.0`
  - `ghcr.io/zhizinan1997/invoice-pdf-extractor:1.0.0`
  - `ghcr.io/zhizinan1997/invoice-pdf-extractor:1.0`
  - `ghcr.io/zhizinan1997/invoice-pdf-extractor:latest`

## Excel 输出

- `明细汇总`：所有发票商品明细合并后的总表
- `发票汇总`：每个 PDF 一行，包含页数、日期、明细条数、价税合计和税额

## 说明

- 当前默认模型为 `gpt-4o`
- 上传的每个 PDF 会被视为一个独立发票文档进行解析
- 如果某些文件识别失败，成功部分仍然会保留并允许下载 Excel
