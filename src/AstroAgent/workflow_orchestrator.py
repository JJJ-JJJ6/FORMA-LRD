import asyncio
import logging
import traceback

from typing import Dict, Any, List, Optional, Set, Union

from langgraph.graph import StateGraph, END, START

from AstroAgent.core.runtime.runtime_container import RuntimeContainer
from AstroAgent.agents.common.state import SpectroState
from AstroAgent.agents.common.base_agent import _is_connection_error, _is_timeout_error
import copy

#########################
from AstroAgent.agents.multi_agents.VisualInterpreter import VisualInterpreter
from AstroAgent.agents.multi_agents.RuleAnalyst import RuleAnalyst
from AstroAgent.agents.multi_agents.SelfEvolve import SelfEvolve
from AstroAgent.agents.multi_agents.AnalysisAuditor import AnalysisAuditor, FeatureAuditor
from AstroAgent.agents.multi_agents.RefinementAssistant import RefinementAssistant
from AstroAgent.agents.multi_agents.SynthesisHost import SynthesisHost
#########################


class WorkflowOrchestrator:
    """
    工作流编排器：管理整个智能体交互流程
    Workflow orchestrator: manage the entire agent interaction workflow
    """

    # 定义语言到代理类的映射
    AGENT_CLASSES = {
        'VisualInterpreter': VisualInterpreter,
        'RuleAnalyst': RuleAnalyst,
        'SelfEvolve': SelfEvolve,
        'AnalysisAuditor': AnalysisAuditor,
        'FeatureAuditor': FeatureAuditor,
        'RefinementAssistant': RefinementAssistant,
        'SynthesisHost': SynthesisHost
    }

    def __init__(
            self,
            configs: Dict[str, Any],
        ):

        self.configs = configs

        # 初始化运行时容器
        self.runtime = RuntimeContainer(self.configs)

        # 初始化所有智能体
        self.spectro_agents = self._initialize_agents()

        # 创建状态图
        self.workflow = self._create_workflow()


        print("🚀 工作流编排器初始化完成")

    def _initialize_agents(self) -> Dict[str, Any]:
        """
        初始化所有智能体
        Initialize all agents
        """
        spectro_agents = {
            '_Visual_Interpreter': self.AGENT_CLASSES['VisualInterpreter'](self.runtime),
            '_Rule_Analyst': self.AGENT_CLASSES['RuleAnalyst'](self.runtime),
            '_Self_Evolve': self.AGENT_CLASSES['SelfEvolve'](self.runtime),
            '_Analysis_Auditor': self.AGENT_CLASSES['AnalysisAuditor'](self.runtime),
            '_Feature_Auditor': self.AGENT_CLASSES['FeatureAuditor'](self.runtime),
            '_Refinement_Assistant': self.AGENT_CLASSES['RefinementAssistant'](self.runtime),
            '_Synthesis_Host': self.AGENT_CLASSES['SynthesisHost'](self.runtime)
        }
        print(f"Initialized {len(spectro_agents)} agents")
        return spectro_agents

    def _check_cancel(self):
        """
        检查是否需要取消分析
        Check if analysis needs to be canceled
        """
        if self.cancel_checker and callable(self.cancel_checker):
            if self.cancel_checker():
                raise asyncio.CancelledError("分析已被用户取消 Analysis canceled by user")

    async def _visual_interpreter_node(self, state: SpectroState) -> SpectroState:
        self._check_cancel()
        print('Stage 1: Visual Interpreter')
        result = await self.spectro_agents["_Visual_Interpreter"].run(state, plot=True)
        self._check_cancel()
        return result

    async def _rule_analyst_node(self, state: SpectroState) -> SpectroState:
        self._check_cancel()
        print('Stage 2: Rule Analyst — Targeted Search')
        result = await self.spectro_agents["_Rule_Analyst"].run(state)
        self._check_cancel()
        return result

    async def _feature_auditor_node(self, state: SpectroState) -> SpectroState:
        self._check_cancel()
        print('Stage 3: Feature Auditor — Cross-Hypothesis Verification')
        result = await self.spectro_agents["_Feature_Auditor"].run(state)
        self._check_cancel()
        return result

    async def _rule_analyst_synthesize_node(self, state: SpectroState) -> SpectroState:
        self._check_cancel()
        print('Stage 4: Rule Analyst — Synthesis')
        result = await self.spectro_agents["_Rule_Analyst"].run_synthesize(state)
        self._check_cancel()
        return result

    async def _analysis_auditor_node(self, state: SpectroState) -> SpectroState:
        self._check_cancel()
        print('Stage 5: Analysis Auditor — Synthesis Audit')
        result = await self.spectro_agents["_Analysis_Auditor"].run(state)
        self._check_cancel()
        return result

    async def _self_evolve_node(self, state: SpectroState) -> SpectroState:
        self._check_cancel()
        print('Stage 2b: Self-Evolve — Ground-truth Check')
        result = await self.spectro_agents["_Self_Evolve"].run(state)
        self._check_cancel()
        return result

    def _create_workflow(self) -> StateGraph:

        workflow = StateGraph(SpectroState)

        workflow.add_node("visual_interpreter", self._visual_interpreter_node)
        workflow.add_node("rule_analyst", self._rule_analyst_node)
        workflow.add_node("feature_auditor", self._feature_auditor_node)
        workflow.add_node("rule_analyst_synthesize", self._rule_analyst_synthesize_node)
        workflow.add_node("analysis_auditor", self._analysis_auditor_node)

        workflow.add_edge(START, 'visual_interpreter')
        workflow.set_entry_point("visual_interpreter")
        workflow.add_edge("visual_interpreter", "rule_analyst")
        workflow.add_edge("rule_analyst", "feature_auditor")
        workflow.add_edge("feature_auditor", "rule_analyst_synthesize")
        workflow.add_edge("rule_analyst_synthesize", "analysis_auditor")
        workflow.add_edge("analysis_auditor", END)

        return workflow.compile()


    async def run_analysis_single(self, state, cancel_checker=None) -> SpectroState:

        print("🚀 Start MCP LLM Spectro Agent")
        # 存储取消检查器
        self.cancel_checker = cancel_checker

        # 保存深拷贝的原始输入，用于重试时恢复干净状态
        initial_state = copy.deepcopy(state)

        max_tries = self.configs.max_tries or 3
        retry_delay = self.configs.retry_delay or 180

        current_state = initial_state

        for attempt in range(max_tries):
            try:
                # 检查取消状态
                self._check_cancel()

                # 运行工作流
                workflow_result = await self.workflow.ainvoke(current_state)
                final_state = workflow_result

                print("✅ 分析流程完成")
                return final_state

            except asyncio.CancelledError as e:
                print(f"⚠️ 分析流程已取消: {e}")
                return current_state

            except Exception as e:
                error_msg = str(e).lower()
                if attempt < max_tries - 1 and _is_connection_error(error_msg):
                    logging.warning(
                        f"🌐 工作流遇到连接错误，{retry_delay}秒后重试..."
                        f" (尝试 {attempt + 1}/{max_tries}): {e}"
                    )
                    await self.runtime.reset_mcp()
                    await asyncio.sleep(retry_delay)
                    # 重置中间结果，从干净状态重跑，避免脏数据（尤其是 List append 字段）污染
                    current_state = copy.deepcopy(initial_state)
                    logging.warning("🧹 已清除中间结果字段，将从头重新执行工作流")
                elif attempt < max_tries - 1 and _is_timeout_error(error_msg):
                    logging.warning(
                        f"⏱️ 工作流遇到超时，{retry_delay}秒后重试..."
                        f" (尝试 {attempt + 1}/{max_tries}): {e}"
                    )
                    await asyncio.sleep(retry_delay)
                    current_state = copy.deepcopy(initial_state)
                    logging.warning("🧹 已清除中间结果字段，将从头重新执行工作流")
                else:
                    print(f"❌ 分析流程失败: {e}")
                    traceback.print_exc()
                    return current_state

        # 所有重试耗尽
        print(f"❌ 分析流程在 {max_tries} 次尝试后仍失败，放弃")
        return current_state
