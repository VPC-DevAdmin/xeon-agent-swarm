"""
Predefined corpus definitions for the Intelligence Report Demo.

Three corpora cover complementary slices of the AI-hardware landscape,
enabling cross-domain questions that showcase swarm advantages:

  ai_hardware    — chips, accelerators, memory interconnects
  ai_software    — frameworks, inference engines, optimization techniques
  llm_landscape  — models, architectures, training & alignment methods

Example complex questions that work well across corpora:
  • "Compare Intel Gaudi and NVIDIA H100 for LLM inference — hardware
    capabilities, software ecosystem maturity, and energy efficiency."
  • "How do quantization techniques interact with AMX / VNNI instructions
    on Xeon to accelerate transformer inference?"
  • "What are the tradeoffs between vLLM speculative decoding and tensor
    parallelism when serving Llama-3 on CPU vs GPU clusters?"
  • "Explain how High Bandwidth Memory bandwidth limits affect multi-head
    attention in large transformer models."
"""
from __future__ import annotations

CORPORA: dict[str, dict] = {
    "ai_hardware": {
        "description": "AI accelerators, CPUs, GPUs, and memory / interconnect systems",
        "wikipedia_titles": [
            "Intel Xeon",
            "AMD EPYC",
            "Nvidia H100",
            "Tensor Processing Unit",
            "Intel Gaudi",
            "Graphcore",
            "Cerebras Systems",
            "Apple M1",
            "ARM Neoverse",
            "High Bandwidth Memory",
            "NVLink",
            "PCI Express",
            "Compute Express Link",
            "Advanced Matrix Extensions",
            "Non-uniform memory access",
            "AI accelerator",
        ],
    },
    "ai_software": {
        "description": "ML frameworks, inference engines, and model-optimization tools",
        "wikipedia_titles": [
            "PyTorch",
            "TensorFlow",
            "JAX (software)",
            "ONNX",
            "CUDA",
            "ROCm",
            "OpenVINO",
            "Hugging Face",
            "DeepSpeed",
            "Quantization (signal processing)",
            "Knowledge distillation",
            "Pruning (artificial neural networks)",
            "Mixed precision",
        ],
    },
    "llm_landscape": {
        "description": "Large language models, architectures, and training / alignment techniques",
        "wikipedia_titles": [
            "GPT-4",
            "LLaMA",
            "Mistral AI",
            "Transformer (deep learning)",
            "Attention mechanism",
            "Retrieval-augmented generation",
            "Reinforcement learning from human feedback",
            "Mixture of experts",
            "Speculative decoding",
            "Fine-tuning (deep learning)",
            "Prompt engineering",
            "Hallucination (artificial intelligence)",
            "Large language model",
        ],
    },
}
