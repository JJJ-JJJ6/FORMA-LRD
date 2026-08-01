# FORMA-LRD（中文说明）

本仓库是上游 [FORMA / LLM-Spectro-Agent](https://github.com/mynamesnoname/FORMA)
多智能体流水线的私有改编版本，用于**核验 JWST/NIRCam F356W 无缝光谱（WFSS）中
Little Red Dot（LRD）与经典 AGN 的宽线证认**（EIGER 巡天，Kapoor+26 样本）。
论文的结论在这里被当作待检验的假设，而不是直接复现的标签。**这个核验流水线是
本项目的核心，下面第 1–5 步搭建的正是它。**

盲搜索——在没有任何先验声明的情况下扫描大量源，找出新候选体，再交给同一个核验
流水线——是一个独立、可选的附加功能，参见
["可选：盲搜索"](#可选盲搜索寻找新候选体)。

- **分支 `lrd`（当前分支）**：全部改编工作。分支 `upstream-baseline`
  是本工作分叉时未经改动的上游提交。
- **逐文件的工作说明**：[LRD_WORK.md](./LRD_WORK.md)（英文）
- **与上游的完整差异**：
  [upstream-baseline...lrd 对比视图](https://github.com/JJJ-JJJ6/FORMA-LRD/compare/upstream-baseline...lrd)

## FORMA 是什么，我们改编了什么

**FORMA** 的全称是 **Formalized Observational Reasoning with Auditable Decisions**
（Wang, Tan 等人，中国科学院上海天文台）。它最初是为 DESI 光学光谱构建的核验层：
LLM 智能体在一维光谱上执行类人的天体物理推断，具体是**源分类**（星系：LRG/ELG、
类星体 QSO）和**类星体的红移估计**——通过生成候选解释、用光谱自身的证据和竞争性
解释对其进行检验，最终给出一个可信度分数，而不是一个孤立的标签。应用于 DESI EDR
专家复核目录时，在中等及以上可信度下，与专家判定类别的二元一致率达到 95.5%。

对于 FORMA-LRD，多智能体架构本身没有改变；改变的是它所核验的领域。本分支
核验的不再是 DESI 光学光谱与 QSO/ELG/LRG 分类，而是 **JWST/NIRCam F356W
无缝光谱（EIGER 巡天，Kapoor+26 样本）中的宽线证认与 LRD-经典 AGN 分类**——
将 DESI 的谱线表、红移引擎（Redrock）和知识库替换为专为该领域构建的近红外
静止系内容，后续还加入了一个可选的盲搜索阶段（见下文），用于在完全没有先验
声明的情况下寻找候选体。

## 系统架构

六个智能体——`VisualInterpreter`、`HypothesisAnalyst`、特征/结果审计器、
`ReportWriter`、`SelfEvolve`——位于 `src/FORMA/agents/multi_agents/`，由
`workflow_orchestrator.py`（LangGraph）编排。每个智能体将一份系统提示词
（`harness/skills/` 下的 skill 文件）与一组可调用工具（峰/双线/BIC 拟合、
CSV 与报告写出）配对，通过调用配置的 `LLM_BASE_URL`/`LLM_MODEL` 端点进行推理。

## 环境要求

- **Python ≥ 3.12**（普通 venv 即可）
- **一个 LLM API key**，任何 OpenAI 兼容端点均可（开发与测试使用 DeepSeek
  `deepseek-v4-pro`）
- 输入数据：目标源的 grizli 抽谱产品 —— `*.1D.fits`（优先）或 `*.stack.fits`（2D）

**不需要**：PaddleOCR / Tesseract（上游已禁用 PNG 输入通道）、Redrock 及其模板
（已被 hypothesis provider 替代）、VLM/视觉模型凭据、Docker（本仓库没有
Dockerfile —— 下文的 venv 承担了这个角色）。

## 1. 安装

```bash
git clone https://github.com/JJJ-JJJ6/FORMA-LRD.git
cd FORMA-LRD                    # lrd 是默认分支
python -m venv .venv
# 激活：.venv\Scripts\activate（Windows）| source .venv/bin/activate（Linux/macOS）
pip install -e .
```

> 若 `import langchain` 报 `langgraph.runtime` 相关错误，说明锁定的 langgraph
> 版本过旧，运行 `pip install -U langgraph` 即可。

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

这里说明的是**核验**场景：一个你已经确认过、带有待核验红移声明的源，通过下面的
`z_spec` 传入。（如果源没有任何先验声明——即盲搜索的输出——省略 `z_spec` 即可，
见["可选：盲搜索"](#可选盲搜索寻找新候选体)。）

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
（`Unknown`、`human_review=Yes`）—— 预期内的校准行为，不是运行失败。

## 5. 测试（可选，无需 pytest，直接运行各文件）

```bash
python lrd_adapt/converter/test_stack_to_forma.py
python lrd_adapt/tools/test_broadline_lsf_bic.py
python lrd_adapt/tools/test_blueshifted_absorption_bic.py
python lrd_adapt/eval/test_anonymizer_isolation.py
python lrd_adapt/eval/test_synthetic_injection.py
python lrd_adapt/eval/test_metrics.py
```

## 可选：盲搜索（寻找新候选体）

可选。上面第 1–5 步就是完整、独立的核验流水线——如果你已经有确定的源和待核验
的声明，可以跳过本节。盲搜索是一个前置筛选阶段，决定哪些源值得送进第 3–4 步；
它不会改变核心流水线本身的运行方式。

1. **粗筛**（`lrd_adapt/blind/triage.py`）：扫描一个装有 `*.stack.fits` 文件的
   文件夹，**不需要红移、不需要先验声明、不调用 LLM**——只做 CWT 谱线检测，
   标记出哪些源显示出真实特征：

   ```bash
   python -m lrd_adapt.blind.triage "path/to/*.stack.fits" \
       -o triage_results.csv --flagged-csv flagged.csv
   ```

2. **重新抽谱**：在你自己的 grizli 环境中（本仓库之外）对被标记的候选体用
   `run_fit=True` 重新抽谱，得到真实的拟合红移。

3. **按上面第 3–4 步转换并运行，只是省略 `z_spec`**——hypothesis provider 会
   生成并公平打分全部六种候选谱线证认，无论该源是否有先验声明。

4. **（自动，无需任何开关）** 如果 `INPUT_DIR` 里转换后的输入文件旁边放着真实的
   `{FILE_NAME}.full.fits`，`VisualInterpreter.py` 会读取其中的拟合红移
   （`lrd_adapt/converter/zfit_reader.py`），把它作为一个带不确定度的软先验
   传入——绝不是硬性覆盖。已在真实 eor1 数据上验证（2026-07-28）：一个约束很弱
   的真实拟合会让打分向其邻域倾斜，但不会把六选一的模糊性错误地收敛成一个虚假
   的确定答案。

5. **（可选，用于新候选体的 Stage B）** `lrd_adapt/evidence/` 可以直接从用户自己
   的 grizli/成像数据中测量光度/颜色（`phot_evidence.py`）和致密度
   （`compactness.py`），这样一个论文里从未提到的全新候选体也能得到
   LRD／经典 AGN 的分类结果，而不是因为缺乏证据而默认输出 `Unknown`。

以上步骤都不会改动 `scripts/main.py` 或智能体流水线本身——它们只是为其提供输入。

## 致谢与许可

基于上游 [FORMA / LLM-Spectro-Agent](https://github.com/mynamesnoname/FORMA)
（MIT 许可）构建。上游项目的原始文档保留在本仓库的 git 历史与
`Quickstart.md` 中；注意其中部分内容（OCR 安装、Redrock、DESI 臂配置、PNG
输入）不适用于本分支。
