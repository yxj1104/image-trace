# 第三方组件与许可证

本文件列出项目直接依赖和随包分发的第三方组件、各自的许可证，以及
再分发时需要履行的义务。

**结论：全链路无 GPL/AGPL 类传染性协议，可自由商用与再分发。**

---

## 一、随包分发的模型权重

| 文件 | 上游项目 | 许可证 | 核实方式 |
|---|---|---|---|
| `models/cn_clip.onnx` | [Chinese-CLIP](https://github.com/OFA-Sys/Chinese-CLIP)（阿里达摩院） | **MIT** | GitHub API `/repos/OFA-Sys/Chinese-CLIP` → `license.spdx_id = MIT` |
| `models/cn_clip_tokenizer.json` | 同上（BERT 词表，随模型分发） | MIT | 同上 |
| `models/face_yunet.onnx` | [OpenCV Zoo](https://github.com/opencv/opencv_zoo) | **Apache-2.0** | GitHub API `/repos/opencv/opencv_zoo` |
| RapidOCR 模型（`rapidocr_onnxruntime/models/`） | [RapidOCR](https://github.com/RapidAI/RapidOCR)（PP-OCRv3 权重） | **Apache-2.0** | 本地包内 LICENSE 首行 |

### 关于 Chinese-CLIP 的保留意见

仓库**代码**是 MIT（已用 GitHub API 实证）。但**模型权重**同时发布在
HuggingFace 与 ModelScope，其模型卡（Model Card）可能存在独立条款。
本次核实未能读取 ModelScope 的模型卡正文（接口返回 HTML 页面）。

本项目的使用方式是「原样分发、不修改、不用于训练其他模型」，符合
上游 README 的常规使用预期。**如需商用，建议自行确认上游模型卡的最新条款。**

另：上游训练数据（约 2 亿中文图文对）的来源与清洗方式未公开，本项目
无法评估该部分合规性。

---

## 二、Python 依赖（源码运行需安装）

| 包 | 版本（实测） | 许可证 |
|---|---|---|
| pillow | 13.0.0 | MIT-CMU |
| numpy | 2.5.5 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |
| imagehash | 4.3.2 | BSD-2-Clause |
| opencv-python-headless | 5.1.0.95 | Apache-2.0 |
| pypdfium2 | 5.15.2 | BSD-3-Clause / Apache-2.0（PDFium 本身为 BSD-3） |
| psd-tools | 1.27.0 | MIT |
| onnxruntime | 1.32.0 | MIT |
| rapidocr-onnxruntime | 1.2.3 | Apache-2.0 |
| scipy | 1.18.1 | BSD-3-Clause |
| resvg-py | 0.5.0 | MIT（resvg 本身为 MPL-2.0，仅通过动态库调用） |
| pywebview | 6.2.1 | BSD-3-Clause |
| pythonnet | 3.2.1 | MIT |
| clr-loader | — | MIT |
| comtypes | 1.5.0 | MIT |
| olefile | 0.47 | BSD-2-Clause |

### 关于 resvg

`resvg-py`（Python 绑定）是 MIT。底层 `resvg`（Rust 实现）是 **MPL-2.0**。
MPL-2.0 是**文件级** copyleft：修改 resvg 源文件才需要开源那些文件，
本项目只是动态链接调用，不修改其源码，因此**不触发开源义务**。

不过如果你要修改 resvg 本体，改动部分需以 MPL-2.0 发布。

---

## 三、系统级依赖（不随包分发）

| 组件 | 用途 | 缺失后果 |
|---|---|---|
| WebView2 Runtime（微软） | 显示界面（pywebview 用它） | 界面打不开。Win11 自带；Win10 1803+ 通常自带，否则需装 |
| Microsoft Visual C++ 2015-2022 运行库 | Python 与多数 C 扩展依赖 | 启动报「找不到 VCRUNTIME140.dll」 |
| .NET Framework 4.7.2+ | pythonnet 需要 | 右键菜单集成失效（核心功能不受影响） |

这三项都是**微软免费分发**的组件，使用者自行安装。本项目不分发它们，
因此不承担其许可证义务。

---

## 四、再分发义务清单

若你把本项目（含打包产物）再分发，需履行：

- [x] 附带本项目 `LICENSE`（MIT）
- [x] 附带本文件 `docs/THIRD_PARTY.md`
- [ ] **Apache-2.0 组件需附带其许可证全文**（RapidOCR、OpenCV Zoo 的 YuNet、
      opencv-python）。仓库内已通过各包的 `dist-info/licenses/` 保留，
      打包产物中同样保留在 `_internal/` 下
- [x] 保留版权声明与归属（本文件已列）
- [ ] 若修改了任何第三方文件，需显著标注修改
- [x] 不使用上游项目名称（Chinese-CLIP / RapidOCR / OpenCV）为本项目背书

**关于第 3 条**：Apache-2.0 §4(a) 要求随分发物提供许可证副本。PyInstaller
打包时会自动带上各包的 `LICENSE` 文件。若你做定制包，请确认这些文件
仍在 `_internal/` 里；若要更严谨，把 `docs/THIRD_PARTY.md` 里的链接
整理成一份 `NOTICE` 一并分发。

---

## 五、本项目自身的许可证

MIT。见仓库根目录 `LICENSE`。

选择 MIT 而非 Apache-2.0 的理由：MIT 更短（不需要 NOTICE 文件、
不需要标注修改），对公司内部采用的门槛最低。本项目没有专利相关诉求，
不需要 Apache-2.0 的专利授权条款保护。
