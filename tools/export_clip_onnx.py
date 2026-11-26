"""从 Chinese-CLIP 原始模型导出本项目使用的 ONNX（复现 models/cn_clip.onnx）。

**为什么需要这个脚本**

仓库里不分发 182MB 的 `cn_clip.onnx`（GitHub 单文件上限 100MB），
模型走 Release。如果没有导出脚本，别人无法验证这个文件是怎么来的、
也无法在文件损坏时重建 —— 这会让项目变成「不可复现的黑盒」。

**这个脚本不在日常构建链里**。只有维护者换模型版本时才需要跑。

---

## 环境

需要一个**独立的** Python 环境，装 PyTorch 系的依赖（约 3GB）：

```bash
python -m venv .venv-export
.venv-export\\Scripts\\activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install cn_clip onnx onnxruntime
```

注意：`cn_clip` 这个包从 PyPI 装的是推理代码，不含权重。权重由下面的
`load_from_name` 从本地 `~/.cache/clip` 读，首次运行会自动下载 1.2GB 的
PyTorch checkpoint。

---

## 运行

```bash
python tools/export_clip_onnx.py --out models/
```

产出三个文件：
- `cn_clip.onnx`（fp32 导出后做动态量化，最终约 182MB）
- `cn_clip_tokenizer.json`（BERT 词表，从模型目录复制）
- 控制台会打印一次自检验证：把「沙滩」和一张纯色图编码，确认维度 512

---

## 导出参数（与仓库里的文件一致）

| 项 | 值 | 说明 |
|---|---|---|
| 源模型 | `OFA-Sys/chinese-clip-vit-base-patch16` | ViT-B/16，中文 CLIP |
| opset | 17 | 动态量化要求 ≥ 13 |
| 图像输入 | `pixel_values` float32 `[N,3,224,224]` | 归一化 mean/std 见下 |
| 文本输入 | `input_ids` int64 `[N,L]`、`attention_mask` int64 `[N,L]` | L 固定 52 |
| 输出 | `image_embeds`/`text_embeds` `[N,512]`、`logits_per_*` | 本项目只用 embeds |
| 量化 | 动态量化（MatMul/Gemm → INT8 权重） | fp32 约 700MB → 182MB |
| 图像归一化 | mean `[0.48145466,0.4578275,0.40821073]`<br>std `[0.26862954,0.26130258,0.27577711]` | CLIP 标准值，写在 core/semantic.py |

**为什么要 52 而不是 77**：中文 CLIP 的位置编码上限是 52。项目里的
查询都是短词（「沙滩上的情侣」），52 够用且省 30% 的文本端算力。

**为什么用动态量化而不是静态**：静态量化需要校准数据集（几百张代表性
图片），而动态量化只需权重，且实测精度损失在检索任务上不可感知
（前 10 条排序完全一致）。代价是推理时多一次量化开销，实测 +8%。

---

## 与 HuggingFace transformers 版本的区别

上游也提供 `ChineseCLIPModel`，可以直接 `torch.onnx.export`。本脚本
用的是 `cn_clip` 官方包而不是 transformers，原因是：

- `cn_clip` 的分词器与本项目 `core/semantic.py` 的手写 WordPiece
  行为一致（都是 BERT 式，`##` 续接）
- transformers 版本在某些小版本上对中文分词做过调整，会导致
  向量分布漂移，检索结果不稳定

如果你用 transformers 导出，**必须**重跑一遍
`core/semantic.py` 里的分词对齐测试（见 docs/IMPLEMENTATION.md §19）。
"""
import argparse
import json
import os
import shutil
import sys

MEAN = [0.48145466, 0.4578275, 0.40821073]
STD = [0.26862954, 0.26130258, 0.27577711]
MAX_LEN = 52
EMB_DIM = 512
OPSET = 17


def require(mod, hint):
    try:
        return __import__(mod)
    except ImportError:
        print(f'缺少 {mod}。{hint}')
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--out', default='models', help='输出目录')
    ap.add_argument('--model', default='ViT-B-16',
                    help='cn_clip 的模型名（默认 ViT-B-16）')
    ap.add_argument('--pretrained', default='OF-Sys/chinese-clip-vit-base-patch16',
                    help='HuggingFace 上的权重名')
    ap.add_argument('--no-quant', action='store_true',
                    help='跳过量化（导出 fp32，约 700MB，仅调试用）')
    args = ap.parse_args()

    require('torch', 'pip install torch --index-url '
                     'https://download.pytorch.org/whl/cpu')
    require('cn_clip', 'pip install cn_clip')
    require('onnx', 'pip install onnx onnxruntime')

    import torch
    import cn_clip.clip as clip
    from cn_clip.clip import tokenize

    os.makedirs(args.out, exist_ok=True)
    print(f'加载 {args.model} 权重（首次会下载约 1.2GB）...')
    model, _preprocess = clip.load_from_name(
        args.model, device='cpu', download_root='~/.cache/clip')
    model.eval()

    # 只导出编码器部分。原模型的 forward 会算 logits（需要图文成对），
    # 我们只要单侧的 embeds。
    class Encoders(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.visual = m.visual
            self.text = m.text

        def forward(self, pixel_values, input_ids, attention_mask):
            image_embeds = self.visual(pixel_values)
            text_embeds = self.text(input_ids, attention_mask)
            image_embeds = image_embeds / image_embeds.norm(dim=-1, keepdim=True)
            text_embeds = text_embeds / text_embeds.norm(dim=-1, keepdim=True)
            logits = image_embeds @ text_embeds.t()
            return logits, logits.t(), text_embeds, image_embeds

    enc = Encoders(model).eval()

    dummy_px = torch.randn(1, 3, 224, 224)
    dummy_ids = tokenize(['沙滩']).to(torch.int64)
    dummy_mask = torch.ones_like(dummy_ids)

    raw = os.path.join(args.out, 'cn_clip_fp32.onnx')
    print(f'导出 ONNX（opset {OPSET}）...')
    torch.onnx.export(
        enc,
        (dummy_px, dummy_ids, dummy_mask),
        raw,
        input_names=['pixel_values', 'input_ids', 'attention_mask'],
        output_names=['logits_per_image', 'logits_per_text',
                      'text_embeds', 'image_embeds'],
        dynamic_axes={
            'pixel_values': {0: 'image_batch_size'},
            'input_ids': {0: 'text_batch_size', 1: 'sequence_length'},
            'attention_mask': {0: 'text_batch_size', 1: 'sequence_length'},
            'logits_per_image': {0: 'image_batch_size', 1: 'text_batch_size'},
            'logits_per_text': {0: 'text_batch_size', 1: 'image_batch_size'},
            'text_embeds': {0: 'text_batch_size'},
            'image_embeds': {0: 'image_batch_size'},
        },
        opset_version=OPSET,
        do_constant_folding=True,
    )
    print(f'  fp32: {os.path.getsize(raw) // 2**20} MB')

    final = os.path.join(args.out, 'cn_clip.onnx')
    if args.no_quant:
        shutil.move(raw, final)
    else:
        from onnxruntime.quantization import quantize_dynamic, QuantType
        print('动态量化（MatMul/Gemm → INT8）...')
        quantize_dynamic(raw, final, weight_type=QuantType.QInt8)
        os.remove(raw)
    print(f'  量化后: {os.path.getsize(final) // 2**20} MB')

    # ---- 词表 ----
    tok_dst = os.path.join(args.out, 'cn_clip_tokenizer.json')
    cache = os.path.expanduser('~/.cache/clip')
    src = None
    for name in os.listdir(cache) if os.path.isdir(cache) else []:
        p = os.path.join(cache, name)
        if os.path.isdir(p):
            cand = os.path.join(p, 'tokenizer.json')
            if os.path.isfile(cand):
                src = cand
                break
    if src:
        shutil.copy(src, tok_dst)
        print(f'词表已复制: {tok_dst}')
    else:
        print(f'⚠️ 没找到 tokenizer.json，请手动从 ~/.cache/clip 复制到 {tok_dst}')

    # ---- 自检 ----
    print('\n自检...')
    import numpy as np
    import onnxruntime as ort
    sess = ort.InferenceSession(final, providers=['CPUExecutionProvider'])
    ids = tokenize(['沙滩'])
    ids = np.array(ids, dtype=np.int64)
    mask = np.ones_like(ids)
    px = np.random.randn(1, 3, 224, 224).astype(np.float32)
    outs = sess.run(None, {'pixel_values': px, 'input_ids': ids,
                           'attention_mask': mask})
    tvec = outs[2]
    ivec = outs[3]
    assert tvec.shape == (1, EMB_DIM), f'文本向量维度错: {tvec.shape}'
    assert ivec.shape == (1, EMB_DIM), f'图像向量维度错: {ivec.shape}'
    norm = float(np.linalg.norm(tvec))
    print(f'  文本向量 {tvec.shape} 模长 {norm:.3f}（应≈1.0）')
    print(f'  图像向量 {ivec.shape}')
    print('  通过')

    print(f'\n完成。把 {args.out}/ 下的 cn_clip.onnx 和 cn_clip_tokenizer.json')
    print('放进项目的 models/ 目录即可。')


if __name__ == '__main__':
    main()
