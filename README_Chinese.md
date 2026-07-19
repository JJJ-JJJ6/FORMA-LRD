# FORMA-LRD（中文说明）

本仓库是上游 [FORMA / LLM-Spectro-Agent](https://github.com/mynamesnoname/FORMA)
多智能体流水线的私有改编版本，用于**核验 JWST/NIRCam F356W 无缝光谱（WFSS）中
Little Red Dot（LRD）与经典 AGN 的宽线证认**（EIGER 巡天，Kapoor+26 样本）。
论文的结论在这里被当作待检验的假设，而不是直接复现的标签。

- **分支 `lrd`（当前分支）**：全部改编工作。分支 `upstream-baseline`
  是本工作分叉时未经改动的上游提交。
- **逐文件的工作说明**：[LRD_WORK.md](./LRD_WORK.md)（英文）
- **与上游的完整差异**：
  [upstream-baseline...lrd 对比视图](https://github.com/JJJ-JJJ6/FORMA-LRD/compare/upstream-baseline...lrd)

以下内容即运行 FORMA-LRD 所需的全部步骤。

## 工作原理 —— "AI 智能体"从哪里来

智能体既不是随仓库分发的成品软件，也不是本地运行的模型 —— 它们只是本仓库中的
普通 Python 类，其"推理能力"来自远程 LLM API：

- **智能体框架（本地，在本仓库中）**：六个智能体（`VisualInterpreter`、
  `HypothesisAnalyst`、特征/结果审计器、`ReportWriter`、`SelfEvolve`）位于
  `src/FORMA/agents/multi_agents/`，由 `workflow_orchestrator.py`（LangGraph）
  串联成流水线。每个智能体 = 一份系统提示词（`harness/skills/` 下的 skill
  文件）+ 一组可调用的分析工具（峰/双线/BIC 拟合、CSV 与报告写出）+ 一个循环：
  反复询问 LLM 下一步做什么，并在本地执行它选择的工具。
- **真正的推理（远程）**：智能体的每一次"思考"都是对配置的 LLM 端点
  （`LLM_BASE_URL`/`LLM_MODEL`）的一次 HTTPS 调用（每个源约 50 次）。你的机器上
  不运行任何语言模型。没有网络或有效的 `LLM_API_KEY` 时，确定性部分（转换器、
  CWT 特征检测、BIC 拟合）仍可工作，但每个智能体都会在第一次 LLM 调用处失败。
- **Docker 与此完全无关**：它至多只是打包 Python 环境的一种方式，而本仓库并无
  Dockerfile —— 下文的 venv 就承担了这个角色。你唯一需要提供的"AI"要素就是
  API key。

## 环境要求

- **Python ≥ 3.12**（普通 venv 即可，不需要 Docker）
- **一个 LLM API key**，任何 OpenAI 兼容端点均可（开发与测试使用 DeepSeek
  `deepseek-v4-pro`）
- 输入数据：目标源的 grizli 抽谱产品 —— `*.1D.fits`（优先）或 `*.stack.fits`（2D）

**明确不需要**（上游的这些功能在本分支已禁用或被替换）：PaddleOCR / Tesseract
（PNG 输入通道已在上游注释掉，OCR 相关安装全部跳过）、Redrock 及其模板
（已被基于论文假设的 hypothesis provider 替代）、VLM/视觉模型凭据、Docker。

## 1. 安装

```bash
git clone https://github.com/JJJ-JJJ6/FORMA-LRD.git
cd FORMA-LRD                    # lrd 是默认分支
python -m venv .venv
# 激活：.venv\Scripts\activate（Windows）| source .venv/bin/activate（Linux/macOS）
pip install -e .
```

> 已知问题：若 `import langchain` 报 `langgraph.runtime` 相关错误，说明锁定的
> langgraph 版本过旧，运行 `pip install -U langgraph` 即可。

验证安装：

```bash
python -c "from FORMA.workflow_orchestrator import WorkflowOrchestrator; print('OK')"
```

## 2. 配置 `.env`

```bash
cp .env_example .env
```

然后在 `.env` 中设置：

**LRD 预设**（来自 `lrd_adapt/configs/f356w.env`，原样复制）：

```ini
REDROCK=false
HYPOTHESIS_PROVIDER=lrd
ARM_NAME=F356W
ARM_WAVELENGTH_RANGE=31500-39500
CWT_MAX_SCALE=14.0
```

**你自己的凭据与路径：**

```ini
LLM_API_KEY=<你的 key>
LLM_BASE_URL=https://api.deepseek.com     # 或任何 OpenAI 兼容端点
LLM_MODEL=deepseek-v4-pro
RUN_MODE=s
INPUT_DIR=<绝对路径>/data/lrd_input
OUTPUT_DIR=<绝对路径>/data/lrd_output
FILE_NAME=                                 # 每次运行时设置，见第 4 步
```

`.env_example` 中的其余项保持默认即可。切勿提交真实的 `.env`（已在
.gitignore 中忽略）。

## 3. 转换输入数据

两条路线产出完全相同格式的 FORMA 可读 FITS。源编号（`SRC02`–`SRC20`）与论文
声称的红移见 `lrd_adapt/eval/mapping.csv` / `lrd_adapt/configs/primary_hypotheses.json`。

**1D 路线（优先 —— grizli 自身的定标最优抽谱）：**

```bash
python -c "from lrd_adapt.converter.grizli_to_forma import convert_grizli_1d_to_forma; \
convert_grizli_1d_to_forma('source.1D.fits', 'data/lrd_input/SRC04.fits', \
arm_name='F356W', source_code='SRC04', z_spec=2.328)"
```

**2D 路线（只有 `*.stack.fits` 二维光谱时 —— 内置 boxcar 抽谱；谱线位置与宽度
可靠，绝对流量未定标）：**

```bash
python -m lrd_adapt.converter.stack_to_forma source.stack.fits data/lrd_input/SRC04.fits \
  --arm F356W --source-code SRC04 --z-spec 2.328
```

## 4. 运行

```bash
# 将 FILE_NAME 设为转换后文件的基本名（不含 .fits），然后：
python scripts/main.py
```

（`FILE_NAME` 可写在 `.env` 中，也可用环境变量传入，例如 Linux/macOS 下
`FILE_NAME=SRC04 python scripts/main.py`。）

结果输出到 `OUTPUT_DIR/<FILE_NAME>/`：

```
final_report.md               ← 六节结构的最终报告（另有 PDF 副本）
visual_interpreter/           ← CWT 特征检测图与 CSV
single_hypothesis/            ← 各假设的智能体运行记录（对话流、谱线表、图）
feature_auditor/  hypothesis_synthesis/  result_auditor/  report_writer/
<FILE_NAME>_redshift_hypotheses.txt   ← hypothesis provider 的打分
```

若某个源未检测到任何特征，流程会提前结束并输出占位报告
（`Unknown / human_review=Yes`）—— 这是设计内的校准行为，不是运行失败。

## 5. 测试（可选，无需 pytest，直接运行各文件）

```bash
python lrd_adapt/converter/test_stack_to_forma.py
python lrd_adapt/tools/test_broadline_lsf_bic.py
python lrd_adapt/tools/test_blueshifted_absorption_bic.py
python lrd_adapt/eval/test_anonymizer_isolation.py
python lrd_adapt/eval/test_synthetic_injection.py
python lrd_adapt/eval/test_metrics.py
```

## 致谢与许可

基于上游 [FORMA / LLM-Spectro-Agent](https://github.com/mynamesnoname/FORMA)
（MIT 许可）构建。上游项目的原始文档保留在本仓库的 git 历史与
`Quickstart.md` 中；注意其中部分内容（OCR 安装、Redrock、DESI 臂配置、PNG
输入）不适用于本分支。
