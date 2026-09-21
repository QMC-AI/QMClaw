# -*- coding: utf-8 -*-
"""
QubitClient Service - LLM/VLM 图像分析服务

提供量子实验图像的 VLM 分析能力，基于 QubitClient 库。
"""

import base64
import io
import json
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from services.base import BaseService, ServiceConfig, run_service, _safe_print


def _log(msg: str):
    """安全日志输出"""
    _safe_print(f"[qubitclient_service] {msg}")


# 支持的实验家族列表（用于 /families 端点）
EXPERIMENT_FAMILIES = [
    {"id": "drag", "name": "DRAG", "description": "DRAG 脉冲校准"},
    {"id": "rabi", "name": "Rabi", "description": "Rabi 振荡"},
    {"id": "rabi_hw", "name": "Rabi HW", "description": "硬件 Rabi 振荡"},
    {"id": "ramsey", "name": "Ramsey", "description": "Ramsey 干涉"},
    {"id": "ramsey_t2star", "name": "T2* Ramsey", "description": "T2* 测量"},
    {"id": "ramsey_freq_cal", "name": "Ramsey Freq Cal", "description": "Ramsey 频率校准"},
    {"id": "ramsey_charge_tomography", "name": "Charge Tomography", "description": "电荷层析成像"},
    {"id": "t1", "name": "T1", "description": "T1 弛豫时间"},
    {"id": "t1_fluctuations", "name": "T1 Fluctuations", "description": "T1 涨落"},
    {"id": "t2", "name": "T2", "description": "T2 退相干时间"},
    {"id": "s21", "name": "S21", "description": "传输测量 S21"},
    {"id": "s21peak", "name": "S21 Peak", "description": "S21 峰值检测"},
    {"id": "s21peakmulti", "name": "S21 Peak Multi", "description": "多 qubit S21 峰值"},
    {"id": "s21vflux", "name": "S21 vs Flux", "description": "S21 随磁通变化"},
    {"id": "spectrum", "name": "Spectrum", "description": "光谱测量"},
    {"id": "spectrum_2d", "name": "Spectrum 2D", "description": "二维光谱"},
    {"id": "qubit_spectroscopy", "name": "Qubit Spectroscopy", "description": " qubit 光谱"},
    {"id": "qubit_flux_spectroscopy", "name": "Qubit Flux Spectroscopy", "description": "Qubit 磁通光谱"},
    {"id": "qubit_spectroscopy_power_frequency", "name": "Qubit Spec Power Freq", "description": "功率频率光谱"},
    {"id": "res_spec", "name": "Resonator Spectroscopy", "description": "谐振器光谱"},
    {"id": "coupler_flux", "name": "Coupler Flux", "description": "耦合器磁通"},
    {"id": "cz_benchmarking", "name": "CZ Benchmarking", "description": "CZ 门基准测试"},
    {"id": "gmm", "name": "GMM", "description": "高斯混合模型"},
    {"id": "optpipulse", "name": "Opt Pi Pulse", "description": "最优 π 脉冲"},
    {"id": "rabicos", "name": "Rabi COS", "description": "Rabi 余弦振荡"},
    {"id": "powershift", "name": "Power Shift", "description": "功率漂移"},
    {"id": "singleshot", "name": "Single Shot", "description": "单次测量"},
    {"id": "rb", "name": "RB", "description": "随机基准测试"},
    {"id": "microwave_ramsey", "name": "Microwave Ramsey", "description": "微波 Ramsey"},
    {"id": "mot_loading", "name": "MOT Loading", "description": "MOT 装载"},
    {"id": "pinchoff", "name": "Pinchoff", "description": "夹断效应"},
    {"id": "pingpong", "name": "PingPong", "description": "乒乓球实验"},
    {"id": "rydberg_ramsey", "name": "Rydberg Ramsey", "description": "Rydberg Ramsey"},
    {"id": "rydberg_spectroscopy", "name": "Rydberg Spectroscopy", "description": "Rydberg 光谱"},
    {"id": "tweezer_array", "name": "Tweezer Array", "description": "光镊阵列"},
]


class QubitClientService(BaseService):
    """QubitClient LLM/VLM 分析服务"""

    def __init__(self, port: int = 3010):
        cfg = ServiceConfig(
            name="qubitclient_service",
            host="localhost",
            port=port,
        )
        super().__init__(cfg)

        # QubitLLM 客户端（延迟初始化）
        self._qubit_llm = None
        self._llm_config: Dict[str, Any] = {}
        self._initialized = False
        self._init_error: Optional[str] = None

    def before_start(self):
        """启动前初始化 - 加载 QubitLLM 配置"""
        _log("Initializing QubitClient service...")
        self._load_config()

    def _load_config(self):
        """加载 QubitClient 配置"""
        try:
            # 尝试从多个位置加载 qubitclient.json
            # server.py 在 Agentic Workflow/qmclaw-server/services/qubitclient_service/
            # 需要向上 4 层到 Agentic Workflow，然后进入 qmclaw-server/config
            server_dir = Path(__file__).parent  # qubitclient_service
            server_dir = server_dir.parent  # services
            server_dir = server_dir.parent  # qmclaw-server
            config_base = server_dir.parent  # Agentic Workflow

            config_paths = [
                server_dir / "config" / "qubitclient.json",  # Agentic Workflow/qmclaw-server/config/
                config_base / "vendor" / "QubitClient" / "qubitclient.json",
                Path.cwd() / "qubitclient.json",
            ]

            config = None
            config_path = None
            for path in config_paths:
                if path.exists():
                    with open(path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                    config_path = path
                    break

            if config and 'llm' in config:
                self._llm_config = {
                    'api_key': config['llm'].get('api_key', ''),
                    'base_url': config['llm'].get('base_url', ''),
                    'model': config['llm'].get('model', ''),
                }
                _log(f"Loaded config from: {config_path}")
                _log(f"Model: {self._llm_config.get('model', 'N/A')}")
                _log(f"Base URL: {self._llm_config.get('base_url', 'N/A')}")
                self._initialized = True
            else:
                self._init_error = "No LLM configuration found in qubitclient.json"
                _log(f"WARNING: {self._init_error}")

        except Exception as e:
            self._init_error = str(e)
            _log(f"Failed to load config: {e}")

    def _get_llm_client(self):
        """获取或初始化 QubitLLM 客户端"""
        if self._qubit_llm is None:
            if not self._initialized:
                raise RuntimeError(f"QubitLLM not initialized: {self._init_error}")

            try:
                from qubitclient.llm import QubitLLM
                self._qubit_llm = QubitLLM(
                    api_key=self._llm_config.get('api_key'),
                    base_url=self._llm_config.get('base_url'),
                    model=self._llm_config.get('model'),
                )
                _log("QubitLLM client initialized")
            except ImportError as e:
                raise RuntimeError(
                    f"Failed to import QubitClient: {e}. "
                    "Install with: pip install -e <repo>/vendor/QubitClient "
                    "(see services/qubitclient_service/requirements.txt)"
                )
            except Exception as e:
                raise RuntimeError(f"Failed to initialize QubitLLM: {e}")

        return self._qubit_llm

    def _decode_image(self, image_data: str) -> Image.Image:
        """解码 base64 图像数据"""
        try:
            # 去除可能的 data URI 前缀
            if ',' in image_data:
                image_data = image_data.split(',', 1)[1]
            image_bytes = base64.b64decode(image_data)
            return Image.open(io.BytesIO(image_bytes))
        except Exception as e:
            raise ValueError(f"Invalid image data: {e}")

    def handle_request(self, method: str, path: str, data: Dict[str, Any], query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理请求"""
        # 路由
        if path == "/health":
            return self._handle_health()
        elif path == "/families":
            return self._handle_families()
        elif path == "/describe":
            return self._handle_describe(data)
        elif path == "/classify":
            return self._handle_classify(data)
        elif path == "/reasoning":
            return self._handle_reasoning(data)
        elif path == "/assess_fit":
            return self._handle_assess_fit(data)
        elif path == "/extract_params":
            return self._handle_extract_params(data)
        elif path == "/evaluate":
            return self._handle_evaluate(data)
        elif path == "/analyze_full":
            return self._handle_analyze_full(data)
        else:
            raise ValueError(f"Unknown path: {path}")

    def _handle_health(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "status": "healthy" if self._initialized else "degraded",
            "service": "qubitclient_service",
            "initialized": self._initialized,
            "model": self._llm_config.get('model', 'N/A'),
            "base_url": self._llm_config.get('base_url', 'N/A'),
            "error": self._init_error,
        }

    def _handle_families(self) -> Dict[str, Any]:
        """获取支持的实验类型列表"""
        return {
            "families": EXPERIMENT_FAMILIES,
            "count": len(EXPERIMENT_FAMILIES),
        }

    def _analyze_task(self, data: Dict[str, Any], task_name: str, task_enum) -> Dict[str, Any]:
        """通用分析任务处理"""
        image_data = data.get("image")
        experiment_family = data.get("experiment_family", "rabi")
        language = data.get("language", "en")

        if not image_data:
            return {"error": "image is required"}

        try:
            # 获取 LLM 客户端
            client = self._get_llm_client()

            # 处理 base64 图像数据（前端传来的格式: "data:image/jpeg;base64,..."）
            image_bytes = image_data
            if isinstance(image_data, str):
                # 去除 data URI 前缀
                if "," in image_data:
                    image_bytes = base64.b64decode(image_data.split(",", 1)[1])
                else:
                    # 不带前缀的 base64 字符串
                    image_bytes = base64.b64decode(image_data)

            # 获取 prompt 和 schema
            prompt_data = client.get_prompt(
                task_enum,
                image_data=image_bytes,  # 传递 bytes 给 get_prompt
                experiment_family=experiment_family,
                language=language,
            )

            messages = prompt_data.get("messages", [])
            response_schema = prompt_data.get("response_schema")

            # 调用 chat（传递 bytes）
            _log(f"Analyzing with task={task_name}, family={experiment_family}, lang={language}")
            result = client.chat(
                messages=messages,
                images=image_bytes,
                response_schema=response_schema,
            )

            return {
                "success": True,
                "task": task_name,
                "experiment_family": experiment_family,
                "result": result,
            }

        except Exception as e:
            _log(f"Analysis error: {e}\n{traceback.format_exc()}")
            return {
                "success": False,
                "error": str(e),
                "task": task_name,
            }

    def _handle_describe(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Q1: 描述图表"""
        from qubitclient.llm import LLMTaskName
        return self._analyze_task(data, "describe_plot", LLMTaskName.DESCRIBE_PLOT)

    def _handle_classify(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Q2: 分类实验结果"""
        from qubitclient.llm import LLMTaskName
        return self._analyze_task(data, "classify_outcome", LLMTaskName.CLASSIFY_OUTCOME)

    def _handle_reasoning(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Q3: 科学推理"""
        from qubitclient.llm import LLMTaskName
        return self._analyze_task(data, "scientific_reasoning", LLMTaskName.SCIENTIFIC_REASONING)

    def _handle_assess_fit(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Q4: 评估拟合"""
        from qubitclient.llm import LLMTaskName
        return self._analyze_task(data, "assess_fit", LLMTaskName.ASSESS_FIT)

    def _handle_extract_params(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Q5: 提取参数"""
        from qubitclient.llm import LLMTaskName
        return self._analyze_task(data, "extract_params", LLMTaskName.EXTRACT_PARAMS)

    def _handle_evaluate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Q6: 评估状态"""
        from qubitclient.llm import LLMTaskName
        return self._analyze_task(data, "evaluate_status", LLMTaskName.EVALUATE_STATUS)

    def _handle_analyze_full(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """完整分析 (Q1-Q6)"""
        image_data = data.get("image")
        experiment_family = data.get("experiment_family", "rabi")
        language = data.get("language", "en")

        if not image_data:
            return {"error": "image is required"}

        try:
            client = self._get_llm_client()
            from qubitclient.llm import LLMTaskName

            # 处理 base64 图像数据
            image_bytes = image_data
            if isinstance(image_data, str):
                if "," in image_data:
                    image_bytes = base64.b64decode(image_data.split(",", 1)[1])
                else:
                    image_bytes = base64.b64decode(image_data)

            # 定义要执行的任务
            tasks = [
                ("describe_plot", LLMTaskName.DESCRIBE_PLOT),
                ("classify_outcome", LLMTaskName.CLASSIFY_OUTCOME),
                ("scientific_reasoning", LLMTaskName.SCIENTIFIC_REASONING),
                ("assess_fit", LLMTaskName.ASSESS_FIT),
                ("extract_params", LLMTaskName.EXTRACT_PARAMS),
                ("evaluate_status", LLMTaskName.EVALUATE_STATUS),
            ]

            results = {}
            errors = {}

            for task_name, task_enum in tasks:
                try:
                    # 获取 prompt
                    prompt_data = client.get_prompt(
                        task_enum,
                        image_data=image_bytes,
                        experiment_family=experiment_family,
                        language=language,
                    )
                    messages = prompt_data.get("messages", [])
                    response_schema = prompt_data.get("response_schema")

                    # 调用 chat
                    result = client.chat(
                        messages=messages,
                        images=image_bytes,
                        response_schema=response_schema,
                    )
                    results[task_name] = result
                    _log(f"  {task_name}: OK")

                except Exception as e:
                    errors[task_name] = str(e)
                    _log(f"  {task_name}: ERROR - {e}")

            return {
                "success": True,
                "experiment_family": experiment_family,
                "results": results,
                "errors": errors if errors else None,
            }

        except Exception as e:
            _log(f"Full analysis error: {e}\n{traceback.format_exc()}")
            return {
                "success": False,
                "error": str(e),
            }

    def get_health(self) -> Dict[str, Any]:
        """获取健康状态"""
        return self._handle_health()


def main():
    """主入口"""
    service = QubitClientService(port=3010)
    run_service(service)


if __name__ == "__main__":
    main()
