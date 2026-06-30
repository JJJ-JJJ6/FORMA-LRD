# LLM-Spectro-Agent

一个由大语言模型（LLM）驱动的智能体，用于对一维天文光谱进行**类人式分析与推断**。

> ⚠️ **当前支持模式：** FITS 输入 + Redrock 红移假设生成。PNG 输入通道尚未完善。

> 📄 相关论文正在准备中。

---

## 项目概述（Overview）

本项目利用大语言模型（LLMs）对一维天文光谱（1D spectra）进行类似人类天文学家的物理推断，当前支持的核心任务包括：

- **天体类型分类**（目前仅支持 galaxy（LRG 和 ELG，输出为 galaxy）、QSO）
- **红移估计**（针对 QSO）

系统设计目标是**模拟人类天文学家的认知流程**，主要包括以下步骤：

1. **光谱图的视觉理解**  
   自动解析坐标轴、单位、整体形态与显著特征
2. **基于规则的物理分析**  
   结合天体物理知识（如 Lyα、C IV、Mg II 等谱线）
3. **多智能体辩论机制**  
   审计代理（auditor）与修正代理（refinement assistant）之间进行推理对抗，以增强鲁棒性
4. **综合总结输出**  
   生成最终分析报告，并给出置信度评估

---

## 使用的模型（Models）

当前流程通过 API 使用以下模型：

- **文本推理模型**：`deepseek-v4-pro`

> ⚠️ 注意：VLM（视觉语言模型）暂时未启用。其他大语言模型尚未测试，如需替换可能需要适配。

---

## 依赖与安装（Dependencies & Installation）

### 1. OCR 引擎

> ⚠️ **注意（2026-06-30）：** PNG 输入通道（依赖 PaddleOCR / Tesseract）已在源码中**注释掉**。当前项目仅支持 `INPUT_FORMAT=fits`。你可以**跳过下方整个 OCR 安装环节**，直接进入 [Python 依赖](#2-python-依赖)。

项目支持两种 OCR（光学字符识别）后端：

- **PaddleOCR（默认）**
- **Tesseract OCR**

默认使用 PaddleOCR，因为其在坐标轴刻度等图像文本识别方面通常更准确，但安装流程相对复杂。

在 `src/utils` 中提供了两个 OCR 封装函数：

```python
_detect_axis_ticks_paddle(state)
```

使用 PaddleOCR。

```python
_detect_axis_ticks_tesseract(state)
```

使用 Tesseract OCR。

你可以在 `.env` 文件中配置所使用的 OCR 引擎。

---

### 1.1 安装 PaddleOCR

PaddleOCR 依赖 PaddlePaddle，需要先安装 PaddlePaddle。

#### （1）安装 PaddlePaddle

**仅 CPU 版本：**

```bash
pip install paddlepaddle==3.2.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
```

如需 GPU 支持或系统相关说明，请参考 PaddlePaddle [官方安装页面](https://www.paddlepaddle.org.cn/)。

#### （2）安装 PaddleOCR

```bash
pip install "paddleocr[all]"
```

#### （3）LangChain 兼容性修复（重要）

当前 PaddleOCR 使用了旧版 LangChain 的导入方式，而本项目依赖新版：

* `langchain-core`
* `langchain-text-splitter`

因此需要在安装完成后**手动修补 PaddleOCR 源码**。

打开以下文件（路径请根据你的 Conda 环境调整）：

```bash
nano ~/Apps/anaconda3/envs/your_env_name/lib/python3.12/site-packages/paddlex/inference/pipelines/components/retriever/base.py
```

将：

```python
from langchain.docstore.document import Document
from langchain.text_splitter import RecursiveCharacterTextSplitter
```

替换为：

```python
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
```

💡 提示：可通过以下命令定位环境路径：

```bash
which python
# 或
conda info --envs
```

---

### 1.2 安装 Tesseract OCR

根据操作系统选择：

* **Ubuntu / Debian**

  ```bash
  sudo apt-get install tesseract-ocr
  ```

* **macOS（Homebrew）**

  ```bash
  brew install tesseract
  ```

* **Windows**
  请从以下地址下载安装：
  [https://github.com/UB-Mannheim/tesseract/wiki](https://github.com/UB-Mannheim/tesseract/wiki)

确认安装成功：

```bash
tesseract --version
```

---

## 2. Python 依赖

安装 Python 依赖包：

```bash
pip install -r requirements.txt
```

---

### 3. 环境变量配置

复制示例配置文件：

```bash
cp .env_example .env
```

并编辑 `.env`，主要参数包括：

* `LLM_API_KEY`、`LLM_MODEL`：文本 LLM 配置
* `VLM_API_KEY`、`VLM_MODEL`：视觉语言模型配置
* `INPUT_DIR`、`OUTPUT_DIR`：输入和输出目录
* `FILE_NAME`：输入 FITS 文件名（不含 `.fits` 扩展名）
* `REDROCK`：设为 `true` 以启用 Redrock 红移假设生成
* `RR_TEMPLATE_DIR`：Redrock 模板目录
* 以及其他控制参数（详见 `.env_example`）

### 4. Redrock 安装（可选，用于红移假设生成）

如果在 `.env` 中设置 `REDROCK=true`，需要安装 Redrock —— DESI 官方红移拟合器。

#### 4.1 安装 Redrock

```bash
git clone https://github.com/desihub/redrock
cd redrock
git clone https://github.com/desihub/redrock-templates py/redrock/templates
pip install -e .
pip install desiutil
pip install desispec
```

模板将随代码一起安装。也可以将模板放在其他位置，并在 `.env` 中设置 `RR_TEMPLATE_DIR` 指向该路径。

#### 4.2 （可选）Archetypes 模式

如果 `USE_ARCHETYPES=true`，克隆 archetype 仓库：

```bash
git clone https://github.com/abhi0395/new-archetypes.git
# 或
git clone https://github.com/desihub/redrock-archetypes.git
```

在 `.env` 中设置 `ARCHETYPE_DIR` 为克隆目录的路径。

验证安装：

```bash
rrdesi --help
```

---

## 快速开始（Quick Start）

详见 [Quick Start](Quickstart.md)

### 运行分析

```bash
python scripts/main.py
```

分析结果将保存至 `.env` 中指定的输出目录。

---

## 输出文件说明（Output Files）

对于输入 FITS 文件 `{your_file_name}.fits`，流程使用 Redrock 生成红移假设，再由多智能体 LLM 分析进行验证和精炼。结果保存至 `{OUTPUT_DIR}/{your_file_name}/`，目录结构如下（以 `116.fits` 为例）：

```
116/
├── 116_in_brief.json                  # 最终简要摘要（类型、红移、置信度、谱线）
├── final_report.md                    # 最终综合报告
├── 116_spec_extract.png               # 基于 OpenCV 的重建光谱
├── 116_spectrum.png                   # 提取的光谱与 SNR 图
├── 116_snapshot.json                  # 完整运行时状态快照
├── 116_brute_force_matching.txt       # 暴力模板匹配结果
├── 116_hypothesis_analysis.txt        # 假设综合裁决（JSON）
├── 116_redrock/                       # Redrock 外部拟合结果
│   ├── 116_redrock.fits
│   └── 116_rrdetails.h5
├── visual_interpreter/                # 视觉解释输出
│   ├── 116_continuum.png              # 拟合连续谱
│   ├── 116_features.png               # 检测到的光谱特征可视化
│   ├── 116_residual_spectrum.png      # 残差光谱（数据 - 连续谱）
│   ├── 116_emission.csv               # 检测到的发射线表
│   ├── 116_absorption.csv             # 检测到的吸收线表
│   └── 116_spectrum.npz               # 提取的光谱数据（NumPy）
├── single_hypothesis/                 # 每个假设的详细分析
│   ├── 1_report.md                    # 假设报告
│   ├── 1_features.png                 # 该红移下的特征可视化
│   ├── 1_lines.csv                    # 识别的谱线
│   ├── 1_lines_cleaned.csv            # 清洗后的谱线列表
│   └── 1_stream.md                    # 智能体流式日志
│   ├── 2_* ...                        # （每个假设一组，最多 N 个）
├── hypothesis_synthesis/              # 多假设综合
│   ├── report.md                      # 综合摘要报告
│   ├── catalog.csv                    # 所有假设的目录
│   └── stream.md                      # 综合智能体流式日志
├── feature_auditor/                   # 特征审计输出
│   ├── stream.md                      # 审计智能体流式日志
│   └── verdict.json                   # 特征裁决
├── result_auditor/                    # 结果审计输出
│   └── stream.md                      # 审计智能体流式日志
└── report_writer/                     # 报告撰写输出
    └── stream.md                      # 撰写智能体流式日志
```

### 核心输出文件

| 文件 | 说明 |
|------|------|
| `{name}_in_brief.json` | 机器可读摘要：类型、红移、置信度、识别的谱线 |
| `final_report.md` | 人类可读的最终报告，包含完整分析细节 |
| `hypothesis_synthesis/report.md` | 所有测试假设的摘要与最终裁决 |
| `hypothesis_synthesis/catalog.csv` | 所有假设及其谱线测量数据表 |
| `visual_interpreter/{name}_emission.csv` | 所有检测到的发射线特征（波长、通量、SNR、宽度） |
| `visual_interpreter/{name}_absorption.csv` | 所有检测到的吸收线特征 |
| `single_hypothesis/{N}_lines.csv` | 每个测试红移的谱线识别结果 |

---

## 系统架构亮点（Architecture Highlights）

* **多智能体辩论机制**
  使用 LangGraph 协调审计与修正代理，提升结果可靠性

* **多尺度谱线特征检测**
  在多个高斯平滑尺度下检测谱峰并稳健合并

* **视觉 + 语言混合架构**
  结合 OpenCV、OCR 与多模态 LLM，实现端到端光谱理解

---

## 许可证（License）

本项目仅用于**科研与教学目的**。

---