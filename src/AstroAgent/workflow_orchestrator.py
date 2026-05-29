import asyncio
import logging

from typing import Dict, Any, List, Optional, Set, Union

from langgraph.graph import StateGraph, END, START

from AstroAgent.core.runtime.runtime_container import RuntimeContainer
from AstroAgent.agents.common.state import SpectroState
from AstroAgent.agents.common.base_agent import _is_connection_error, _is_timeout_error
import copy

#########################
# To be done
from AstroAgent.agents.multi_agents.VisualInterpreter import VisualInterpreter
from AstroAgent.agents.multi_agents.RuleAnalyst import RuleAnalyst
from AstroAgent.agents.multi_agents.SelfEvolve import SelfEvolve
from AstroAgent.agents.multi_agents.AnalysisAuditor import AnalysisAuditor
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
        'RefinementAssistant': RefinementAssistant,
        'SynthesisHost': SynthesisHost
    }
    
    def __init__(
            self, 
            configs: Dict[str, Any],
        ):

        self.configs = configs

        # language = self.configs.language  # 默认使用 'CN'

        # self.agent_classes = self.AGENT_CLASSES.get(language, self.AGENT_CLASSES["CN"])

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
            '_Refinement_Assistant': self.AGENT_CLASSES['RefinementAssistant'](self.runtime),
            '_Synthesis_Host': self.AGENT_CLASSES['SynthesisHost'](self.runtime)
        }
        # print(f"初始化了 {len(spectro_agents)} 个智能体")
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
        print('Stage 2: Rule Analyst')
        result = await self.spectro_agents["_Rule_Analyst"].run(state)
        self._check_cancel()
        return result
    
    async def _analysis_auditor_critique_node(self, state: SpectroState) -> SpectroState:
        self._check_cancel()
        print('Stage 3a: Analysis Auditor — Per-path Critique')
        result = await self.spectro_agents["_Analysis_Auditor"].run(state)
        self._check_cancel()
        return result

    async def _refinement_assistant_patch_node(self, state: SpectroState) -> SpectroState:
        self._check_cancel()
        print('Stage 3b: Refinement Assistant — Per-path Patch')
        result = await self.spectro_agents["_Refinement_Assistant"].run(state)
        self._check_cancel()
        return result

    async def _analysis_auditor_verdict_node(self, state: SpectroState) -> SpectroState:
        self._check_cancel()
        print('Stage 3c: Analysis Auditor — Cross-type Verdict')
        result = await self.spectro_agents["_Analysis_Auditor"].run_verdict(state)
        self._check_cancel()
        return result

    async def _refinement_assistant_final_node(self, state: SpectroState) -> SpectroState:
        self._check_cancel()
        print('Stage 3d: Refinement Assistant — Final Report')
        result = await self.spectro_agents["_Refinement_Assistant"].run_final(state)
        self._check_cancel()
        return result
    
    async def _self_evolve_node(self, state: SpectroState) -> SpectroState:
        self._check_cancel()
        print('Stage 2b: Self-Evolve — Ground-truth Check')
        result = await self.spectro_agents["_Self_Evolve"].run(state)
        self._check_cancel()
        return result

    async def _synthesis_host_node(self, state: SpectroState) -> SpectroState:
        self._check_cancel()
        print('Stage 4: Synthesis Host')
        result = await self.spectro_agents["_Synthesis_Host"].run(state)
        self._check_cancel()
        return result
    
    def _should_self_evolve(self, state: SpectroState) -> str:
        """Conditional routing: run SelfEvolve only when self_evolve is enabled."""
        if self.runtime.configs.params.self_evolve:
            return "self_evolve"
        return "skip"

    def _should_continue_discussion(self, state: SpectroState) -> str:
        """Conditional routing: continue the critique→patch loop or proceed to verdict."""
        max_rounds = state.get('discussion_rounds')
        if max_rounds is None:
            max_rounds = self.runtime.configs.params.discussion_rounds
        max_rounds = max(1, max_rounds)
        current = state.get('current_discussion_round', 0)
        if current < max_rounds:
            return "continue"
        return "verdict"

    def _create_workflow(self) -> StateGraph:

        workflow = StateGraph(SpectroState)

        workflow.add_node("visual_interpreter", self._visual_interpreter_node)
        workflow.add_node("rule_analyst", self._rule_analyst_node)
        workflow.add_node("self_evolve", self._self_evolve_node)
        workflow.add_node("analysis_auditor_critique", self._analysis_auditor_critique_node)
        workflow.add_node("refinement_assistant_patch", self._refinement_assistant_patch_node)
        workflow.add_node("analysis_auditor_verdict", self._analysis_auditor_verdict_node)
        workflow.add_node("refinement_assistant_final", self._refinement_assistant_final_node)
        workflow.add_node("synthesis_host", self._synthesis_host_node)

        workflow.add_edge(START, 'visual_interpreter')
        workflow.set_entry_point("visual_interpreter")
        workflow.add_edge("visual_interpreter", "rule_analyst")
        # Conditional: SelfEvolve (when self_evolve=true) or skip to next stage
        workflow.add_conditional_edges(
            "rule_analyst",
            self._should_self_evolve,
            {
                "self_evolve": "self_evolve",
                "skip": END,  # TODO: 测试用，测完改为 analysis_auditor_critique
            }
        )
        workflow.add_edge("self_evolve", END)  # TODO: 测试用，测完改为 analysis_auditor_critique
        # workflow.add_edge("rule_analyst", "analysis_auditor_critique")
        workflow.add_edge("analysis_auditor_critique", "refinement_assistant_patch")
        # Loop: patch → critique if more rounds needed, else → verdict
        workflow.add_conditional_edges(
            "refinement_assistant_patch",
            self._should_continue_discussion,
            {
                "continue": "analysis_auditor_critique",
                "verdict": "analysis_auditor_verdict",
            }
        )
        workflow.add_edge("analysis_auditor_verdict", "refinement_assistant_final")
        workflow.add_edge("refinement_assistant_final", "synthesis_host")
        workflow.add_edge("synthesis_host", END)

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
                    return current_state

        # 所有重试耗尽
        print(f"❌ 分析流程在 {max_tries} 次尝试后仍失败，放弃")
        return current_state