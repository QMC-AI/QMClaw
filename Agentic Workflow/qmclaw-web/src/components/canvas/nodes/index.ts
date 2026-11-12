/**
 * Node Components Index
 *
 * Registry of all node types for React Flow
 */

import ExperimentNode from './ExperimentNode';
import PrintNode from './PrintNode';
import AnalyzeNode from './AnalyzeNode';
import QualityGateNode from './QualityGateNode';
import WhileNode from './WhileNode';
import ParallelNode from './ParallelNode';
import NotifyNode from './NotifyNode';
import DefaultNode from './DefaultNode';
import DecisionNode from './DecisionNode';
import ImageAnalysisNode from './ImageAnalysisNode';
import ImageClassificationNode from './ImageClassificationNode';
import AdjustParamsNode from './AdjustParamsNode';
import ContextNode from './ContextNode';
import ContrastNode from './ContrastNode';
import CodeNode from './CodeNode';
import LlmNode from './LlmNode';
import DslUnknownNode from './DslUnknownNode';

export const nodeTypes = {
  experiment: ExperimentNode,
  print: PrintNode,
  analyze: AnalyzeNode,
  quality_gate: QualityGateNode,
  while: WhileNode,
  parallel: ParallelNode,
  notify: NotifyNode,
  decision: DecisionNode,
  image_analysis: ImageAnalysisNode,
  image_classification: ImageClassificationNode,
  adjust_params: AdjustParamsNode,
  context: ContextNode,
  contrast: ContrastNode,
  code: CodeNode,
  llm: LlmNode,
  dsl_unknown: DslUnknownNode,
  // Fallback
  workflowNode: DefaultNode,
};

export { ExperimentNode, PrintNode, AnalyzeNode, QualityGateNode, WhileNode, ParallelNode, NotifyNode, DefaultNode, DecisionNode, ImageAnalysisNode, ImageClassificationNode, AdjustParamsNode, ContextNode, ContrastNode, CodeNode, LlmNode, DslUnknownNode };
