# models/ — 模型文件说明

这个目录放运行所需的 ONNX 模型。**模型不进 Git 仓库**（`cn_clip.onnx` 有
182MB，超过 GitHub 单文件 100MB 上限），获取方式见下。

## 需要的三个文件

| 文件 | 大小 | 用途 | 缺失后果 |
|---|---|---|---|
| `cn_clip.onnx` | 182MB | 画面语义搜索（中文 CLIP 图像/文本编码器） | 「画面」搜索不可用 |
| `cn_clip_tokenizer.json` | 429KB | 上面模型的 BERT 词表 | 同上 |
| `face_yunet.onnx` | 227KB | 人脸检测（人物搜索） | 「人物」搜索不可用 |

## 获取方式

**推荐：用 Release 里的完整包**

到 [Releases](../../releases) 下载 `溯源-x.y.z-win64.zip`，解压即用。
完整包里已经含全部模型，不需要单独处理。

**仅源码运行：**

```bash
# 从 Release 的 assets 单独下载模型包
# （每个 Release 都附带 models-x.y.z.zip，约 183MB）
unzip models-x.y.z.zip -d models/
```

**自己从原始模型导出：**

见 `tools/export_clip_onnx.py` 的说明。它会从 HuggingFace / ModelScope
拉取 `OFA-Sys/chinese-clip-vit-base-patch16`，导出 ONNX 并做动态量化，
产出与本目录中文件等价的结果。

## 许可证

| 模型 | 来源 | 协议 |
|---|---|---|
| cn_clip.onnx | [Chinese-CLIP](https://github.com/OFA-Sys/Chinese-CLIP)（阿里达摩院，MIT） | MIT |
| cn_clip_tokenizer.json | 同上（随 BERT 词表分发） | MIT |
| face_yunet.onnx | [OpenCV Zoo](https://github.com/opencv/opencv_zoo)（Apache-2.0） | Apache-2.0 |

⚠️ Chinese-CLIP 的 **代码** 是 MIT（已用 GitHub API 核实）。**模型权重** 发布在
HuggingFace / ModelScope，其模型卡可能存在独立条款。本仓库对权重的使用方式是
「原样分发、不修改、不用于训练其他模型」。如需商用，建议自行确认上游模型卡的
最新条款。

模型训练数据（约 2 亿中文图文对）的来源与清洗方式上游未公开，本仓库无法评估。
