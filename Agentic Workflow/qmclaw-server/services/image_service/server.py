"""
services/image_service/server.py - 图像服务

提供图像分类和模型管理功能：
- PyTorch/ONNX 推理
- 模型训练
- 图像分类
"""

import json
import time
import threading
import sys
import os
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..base import BaseService, ServiceConfig, run_service, _safe_print
from ..common import setup_logging, config


def _log(msg: str):
    """安全日志输出"""
    _safe_print(f"[image_service] {msg}")


# 模型和图像目录
MODEL_DIR = Path(__file__).parent.parent.parent.parent / "models"
IMAGE_DIR = Path(__file__).parent.parent.parent.parent / "images"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
IMAGE_DIR.mkdir(parents=True, exist_ok=True)


class ImageService(BaseService):
    """图像服务

    核心功能:
    - 图像分类推理
    - 模型训练
    - 模型管理
    """

    def __init__(self, port: int = 3007):
        cfg = ServiceConfig(
            name="image_service",
            host="localhost",
            port=port,
        )
        super().__init__(cfg)

        # 模型加载状态
        self._model: Optional[Any] = None
        self._model_backend: str = "pytorch"  # pytorch or onnx
        self._model_loaded: bool = False
        self._model_lock = threading.Lock()

        # 分类标签
        self._class_names: List[str] = ["good", "bad"]
        self._model_path = MODEL_DIR / "classifier.pth"

        # 训练状态
        self._training: bool = False
        self._training_progress: float = 0.0

        _log("Image service initialized")
        _log(f"Model dir: {MODEL_DIR}")
        _log(f"Image dir: {IMAGE_DIR}")

    def _load_model(self) -> bool:
        """加载模型"""
        with self._model_lock:
            if self._model_loaded:
                return True

            if not self._model_path.exists():
                _log(f"Model file not found: {self._model_path}")
                return False

            try:
                if self._model_backend == "pytorch":
                    import torch
                    import torch.nn as nn

                    # 简单的 CNN 模型
                    class SimpleCNN(nn.Module):
                        def __init__(self, num_classes=2):
                            super().__init__()
                            self.features = nn.Sequential(
                                nn.Conv2d(3, 16, 3, padding=1),
                                nn.ReLU(),
                                nn.MaxPool2d(2),
                                nn.Conv2d(16, 32, 3, padding=1),
                                nn.ReLU(),
                                nn.MaxPool2d(2),
                                nn.Conv2d(32, 64, 3, padding=1),
                                nn.ReLU(),
                                nn.MaxPool2d(2),
                            )
                            self.classifier = nn.Sequential(
                                nn.Flatten(),
                                nn.Linear(64 * 28 * 28, 128),
                                nn.ReLU(),
                                nn.Dropout(0.5),
                                nn.Linear(128, num_classes),
                            )

                        def forward(self, x):
                            x = self.features(x)
                            x = self.classifier(x)
                            return x

                    self._model = SimpleCNN(num_classes=len(self._class_names))
                    self._model.load_state_dict(torch.load(self._model_path, map_location='cpu'))
                    self._model.eval()
                    self._model_loaded = True
                    _log("PyTorch model loaded")

                elif self._model_backend == "onnx":
                    import onnxruntime as ort
                    self._model = ort.InferenceSession(str(self._model_path.with_suffix('.onnx')))
                    self._model_loaded = True
                    _log("ONNX model loaded")

                return self._model_loaded

            except Exception as e:
                _log(f"Model load error: {e}\n{traceback.format_exc()}")
                return False

    def _classify_image(self, image_path: str, threshold: float = 0.75) -> Dict[str, Any]:
        """分类单张图像"""
        if not self._load_model():
            return {"error": "Model not loaded"}

        try:
            from PIL import Image
            import torch
            from torchvision import transforms

            # 加载并预处理图像
            img = Image.open(image_path).convert('RGB')
            transform = transforms.Compose([
                transforms.Resize((224, 224)),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
            ])
            img_tensor = transform(img).unsqueeze(0)

            # 推理
            with torch.no_grad():
                outputs = self._model(img_tensor)
                probabilities = torch.softmax(outputs, dim=1)[0]
                predicted_class = torch.argmax(probabilities).item()
                confidence = probabilities[predicted_class].item()

            result = {
                "class": self._class_names[predicted_class],
                "confidence": float(confidence),
                "probabilities": {
                    name: float(prob) for name, prob in zip(self._class_names, probabilities.tolist())
                },
                "needs_review": confidence < threshold,
            }

            return result

        except Exception as e:
            _log(f"Classification error: {e}\n{traceback.format_exc()}")
            return {"error": str(e)}

    def _classify_folder(self, folder_path: str, threshold: float = 0.75, margin: float = 0.15) -> Dict[str, Any]:
        """批量分类文件夹中的图像"""
        if not self._load_model():
            return {"error": "Model not loaded"}

        folder = Path(folder_path)
        if not folder.exists():
            return {"error": f"Folder not found: {folder_path}"}

        # 获取所有图像文件
        image_extensions = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff'}
        image_files = [f for f in folder.iterdir() if f.suffix.lower() in image_extensions]

        if not image_files:
            return {"error": "No images found in folder", "results": []}

        results = []
        stats = {"good": 0, "bad": 0, "review": 0}

        for img_path in image_files:
            result = self._classify_image(str(img_path), threshold)
            if "error" not in result:
                result["filename"] = img_path.name
                results.append(result)

                # 统计
                if result["needs_review"]:
                    stats["review"] += 1
                elif result["class"] == "good":
                    stats["good"] += 1
                else:
                    stats["bad"] += 1

        return {
            "total": len(results),
            "stats": stats,
            "results": results,
        }

    def _train_model(self, epochs: int = 20, batch_size: int = 32, imbalance_mode: str = "weighted") -> Dict[str, Any]:
        """训练模型"""
        if self._training:
            return {"error": "Training already in progress"}

        # 检查训练数据
        train_dir = IMAGE_DIR / "train"
        if not train_dir.exists():
            return {"error": f"Training data not found: {train_dir}"}

        self._training = True
        self._training_progress = 0.0

        def train_async():
            try:
                import torch
                import torch.nn as nn
                from torch.utils.data import DataLoader, ImageFolder
                from torchvision import transforms

                _log(f"Starting training: epochs={epochs}, batch_size={batch_size}")

                # 数据增强
                transform = transforms.Compose([
                    transforms.Resize((224, 224)),
                    transforms.RandomHorizontalFlip(),
                    transforms.RandomRotation(10),
                    transforms.ColorJitter(brightness=0.2, contrast=0.2),
                    transforms.ToTensor(),
                    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
                ])

                # 加载数据
                dataset = ImageFolder(str(train_dir), transform=transform)
                dataloader = DataLoader(dataset, batch_size=batch_size, shuffle=True)

                num_classes = len(dataset.classes)
                self._class_names = dataset.classes

                # 创建模型
                class SimpleCNN(nn.Module):
                    def __init__(self, num_classes=2):
                        super().__init__()
                        self.features = nn.Sequential(
                            nn.Conv2d(3, 16, 3, padding=1),
                            nn.ReLU(),
                            nn.MaxPool2d(2),
                            nn.Conv2d(16, 32, 3, padding=1),
                            nn.ReLU(),
                            nn.MaxPool2d(2),
                            nn.Conv2d(32, 64, 3, padding=1),
                            nn.ReLU(),
                            nn.MaxPool2d(2),
                        )
                        self.classifier = nn.Sequential(
                            nn.Flatten(),
                            nn.Linear(64 * 28 * 28, 128),
                            nn.ReLU(),
                            nn.Dropout(0.5),
                            nn.Linear(128, num_classes),
                        )

                    def forward(self, x):
                        x = self.features(x)
                        x = self.classifier(x)
                        return x

                model = SimpleCNN(num_classes=num_classes)

                # 损失函数（处理类别不平衡）
                if imbalance_mode == "weighted":
                    class_counts = [0] * num_classes
                    for _, label in dataset.samples:
                        class_counts[label] += 1
                    weights = [1.0 / c if c > 0 else 1.0 for c in class_counts]
                    criterion = nn.CrossEntropyLoss(weight=torch.tensor(weights))
                else:
                    criterion = nn.CrossEntropyLoss()

                optimizer = torch.optim.Adam(model.parameters(), lr=0.001)

                # 训练循环
                for epoch in range(epochs):
                    model.train()
                    total_loss = 0.0
                    correct = 0
                    total = 0

                    for batch_idx, (images, labels) in enumerate(dataloader):
                        optimizer.zero_grad()
                        outputs = model(images)
                        loss = criterion(outputs, labels)
                        loss.backward()
                        optimizer.step()

                        total_loss += loss.item()
                        _, predicted = torch.max(outputs.data, 1)
                        total += labels.size(0)
                        correct += (predicted == labels).sum().item()

                        self._training_progress = (epoch + (batch_idx + 1) / len(dataloader)) / epochs

                    accuracy = 100 * correct / total
                    avg_loss = total_loss / len(dataloader)
                    _log(f"Epoch {epoch + 1}/{epochs}, Loss: {avg_loss:.4f}, Accuracy: {accuracy:.2f}%")

                # 保存模型
                model.eval()
                torch.save(model.state_dict(), self._model_path)
                _log(f"Model saved to {self._model_path}")

                self._model = model
                self._model_loaded = True

            except Exception as e:
                _log(f"Training error: {e}\n{traceback.format_exc()}")
            finally:
                self._training = False
                self._training_progress = 1.0

        thread = threading.Thread(target=train_async)
        thread.daemon = True
        thread.start()

        return {"success": True, "message": "Training started"}

    def handle_request(self, method: str, path: str, data: Dict[str, Any], query: Dict[str, List[str]]) -> Dict[str, Any]:
        """处理图像请求"""
        if path == "/health":
            return self._handle_health()
        elif path == "/classify/single":
            return self._handle_classify_single(data)
        elif path == "/classify/folder":
            return self._handle_classify_folder(data)
        elif path == "/train":
            return self._handle_train(data)
        elif path == "/model/info":
            return self._handle_model_info()
        elif path == "/training/status":
            return self._handle_training_status()
        else:
            raise ValueError(f"Unknown path: {path}")

    def _handle_health(self) -> Dict[str, Any]:
        """健康检查"""
        return {
            "status": "healthy",
            "service": "image_service",
            "model_loaded": self._model_loaded,
            "model_backend": self._model_backend,
            "training": self._training,
        }

    def _handle_classify_single(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """分类单张图像"""
        image_path = data.get("imagePath", "")
        threshold = data.get("threshold", 0.75)

        if not image_path:
            return {"error": "imagePath is required"}

        return self._classify_image(image_path, threshold)

    def _handle_classify_folder(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """批量分类"""
        folder_path = data.get("folderPath", "")
        threshold = data.get("threshold", 0.75)
        margin = data.get("margin", 0.15)

        if not folder_path:
            return {"error": "folderPath is required"}

        return self._classify_folder(folder_path, threshold, margin)

    def _handle_train(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """训练模型"""
        epochs = data.get("epochs", 20)
        batch_size = data.get("batchSize", 32)
        imbalance_mode = data.get("imbalanceMode", "weighted")

        return self._train_model(epochs, batch_size, imbalance_mode)

    def _handle_model_info(self) -> Dict[str, Any]:
        """获取模型信息"""
        return {
            "model_path": str(self._model_path),
            "model_exists": self._model_path.exists(),
            "model_loaded": self._model_loaded,
            "backend": self._model_backend,
            "classes": self._class_names,
        }

    def _handle_training_status(self) -> Dict[str, Any]:
        """获取训练状态"""
        return {
            "training": self._training,
            "progress": self._training_progress,
        }

    def get_health(self) -> Dict[str, Any]:
        """获取健康状态"""
        return {
            "status": "healthy",
            "service": "image_service",
            "model_loaded": self._model_loaded,
            "training": self._training,
        }


def main():
    """主入口"""
    service = ImageService(port=3007)
    run_service(service)


if __name__ == "__main__":
    main()
