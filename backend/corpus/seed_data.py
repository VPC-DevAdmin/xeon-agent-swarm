"""
Predefined corpus definitions for the Intelligence Report Demo.

Five corpora cover the full range of canonical demo queries:

  ai_hardware      — chips, accelerators, memory, interconnects, benchmarks
  ai_software      — frameworks, inference engines, quantization, optimization
  llm_landscape    — models, architectures, training & alignment methods
  energy_climate   — power generation, energy economics, carbon, renewables
  economics_society — remote work, real estate, urban economics, labour markets

Example complex questions that work well across corpora:
  • "Compare Intel Xeon and NVIDIA H100 for LLM inference — hardware
    capabilities, software ecosystem maturity, and energy efficiency."
  • "How do quantization techniques interact with AMX / VNNI instructions
    on Xeon to accelerate transformer inference?"
  • "What are the tradeoffs between vLLM speculative decoding and tensor
    parallelism when serving Llama-3 on CPU vs GPU clusters?"
  • "Compare the energy efficiency of nuclear, wind, and solar power
    generation, including current costs per MWh and carbon footprint."
  • "Explain the differences between transformer and LSTM architectures
    for NLP, with code examples of each."
  • "Analyze the economic impacts of remote work on urban real estate
    markets since 2020."
"""
from __future__ import annotations

CORPORA: dict[str, dict] = {

    # ── AI Hardware ────────────────────────────────────────────────────────────
    # Chips, accelerators, memory technologies, interconnects, benchmark suites.
    # Covers the full stack from die-level features (AMX, HBM, NVLink) to
    # system-level benchmarks (MLPerf) and competing vendor platforms.
    "ai_hardware": {
        "description": "AI accelerators, CPUs, GPUs, memory systems, interconnects, and benchmarks",
        "wikipedia_titles": [
            # ── Intel ─────────────────────────────────────────────────────────
            "Intel Xeon",
            "Advanced Matrix Extensions",       # AMX — INT8/BF16 TMUL on Sapphire Rapids+
            "Intel Gaudi",                      # Habana/Gaudi AI accelerator
            "Habana Labs",                      # Intel's AI accelerator subsidiary
            "Intel Arc",                        # Intel GPU / integrated AI accelerator
            "Intel oneAPI",                     # Unified programming model for CPU+GPU+FPGA

            # ── AMD ───────────────────────────────────────────────────────────
            "AMD EPYC",
            "AMD Instinct",                     # MI300X and MI series GPU accelerators

            # ── NVIDIA ────────────────────────────────────────────────────────
            "Nvidia H100",
            "Tensor core",                      # NVIDIA tensor core for matrix math
            "CUDA",
            "NVLink",
            "NVIDIA A100",

            # ── Other accelerators ────────────────────────────────────────────
            "Tensor Processing Unit",           # Google TPU
            "Graphcore",
            "Cerebras Systems",
            "SambaNova Systems",
            "Groq (company)",                   # LPU — Language Processing Unit
            "Apple M1",
            "ARM Neoverse",
            "AI accelerator",

            # ── Memory & interconnects ────────────────────────────────────────
            "High Bandwidth Memory",
            "PCI Express",
            "Compute Express Link",
            "InfiniBand",                       # High-speed interconnect for GPU clusters
            "RDMA over Converged Ethernet",     # RoCE — data-center networking
            "Non-uniform memory access",
            "Cache (computing)",

            # ── Packaging & manufacturing ─────────────────────────────────────
            "Chiplet",                          # Modular die packaging (AMD, Intel)
            "Three-dimensional integrated circuit",  # 3D stacking (CoWoS, SoIC)
            "Silicon photonics",                # Optical interconnects between chips

            # ── Benchmarks & standards ────────────────────────────────────────
            "MLCommons",                        # MLPerf benchmark organisation
            "FLOPS",                            # Floating-point operations per second
            "Roofline model",                   # Compute vs memory-bound analysis
        ],
    },

    # ── AI Software ───────────────────────────────────────────────────────────
    # Frameworks, inference engines, quantization algorithms, and system-level
    # optimization techniques. Covers both training and production serving.
    "ai_software": {
        "description": "ML frameworks, inference engines, quantization, and model-optimization tools",
        "wikipedia_titles": [
            # ── Deep learning frameworks ──────────────────────────────────────
            "PyTorch",
            "TensorFlow",
            "JAX (software)",
            "Keras",
            "Apache MXNet",

            # ── Inference & serving ───────────────────────────────────────────
            "OpenVINO",                         # Intel's inference optimization toolkit
            "TensorRT",                         # NVIDIA inference optimizer
            "ONNX",                             # Open Neural Network Exchange format
            "Hugging Face",                     # Model hub + Transformers library
            "DeepSpeed",                        # Microsoft distributed training + inference

            # ── Quantization & compression ────────────────────────────────────
            "Quantization (signal processing)", # INT8/FP8/INT4 weight quantization
            "Knowledge distillation",
            "Pruning (artificial neural networks)",
            "Mixed precision",
            "Low-rank approximation",           # Foundation for LoRA fine-tuning

            # ── Parallel & distributed computing ─────────────────────────────
            "Data parallelism",
            "Model parallelism",                # Splitting model layers across devices
            "ROCm",                             # AMD GPU software stack
            "CUDA",
            "Basic Linear Algebra Subprograms", # BLAS — backbone of neural net math
            "AVX-512",                          # Intel SIMD for vectorized math
            "OpenMP",                           # Multi-thread parallelism on CPU

            # ── Numerical computing ───────────────────────────────────────────
            "Floating-point arithmetic",
            "Bfloat16 floating-point format",   # BF16 — used in AMX and A100
            "Automatic differentiation",        # Backprop foundation

            # ── Memory efficiency techniques ──────────────────────────────────
            "Gradient checkpointing",
            "In-context learning (natural language processing)",
        ],
    },

    # ── LLM Landscape ─────────────────────────────────────────────────────────
    # Language models, core architectures, training methods, alignment, and
    # evaluation. Covers both the historical progression and current SOTA.
    "llm_landscape": {
        "description": "Large language models, architectures, training methods, and evaluation",
        "wikipedia_titles": [
            # ── Flagship models ───────────────────────────────────────────────
            "GPT-4",
            "GPT-3",
            "ChatGPT",
            "LLaMA",
            "Mistral AI",
            "Gemini (language model)",
            "Claude (language model)",          # Anthropic Claude

            # ── Core architecture ─────────────────────────────────────────────
            "Transformer (deep learning)",
            "Attention mechanism",
            "Long short-term memory",           # LSTM — comparison target for Transformer
            "Recurrent neural network",         # RNN — context for LSTM
            "Gated recurrent unit",             # GRU — lighter-weight RNN alternative
            "Encoder–decoder architecture",
            "Residual neural network",          # ResNet — skip connections used in Transformers
            "Layer normalization",
            "Positional encoding",

            # ── Efficient architectures ───────────────────────────────────────
            "Mixture of experts",
            "Speculative decoding",
            "Grouped-query attention",
            "Sliding window attention",
            "State space model",                # Mamba / SSM alternative to Transformers

            # ── Training & alignment ──────────────────────────────────────────
            "Reinforcement learning from human feedback",
            "Fine-tuning (deep learning)",
            "Transfer learning",
            "Retrieval-augmented generation",
            "Prompt engineering",
            "In-context learning (natural language processing)",

            # ── Evaluation & safety ───────────────────────────────────────────
            "Hallucination (artificial intelligence)",
            "Perplexity (information theory)",  # Primary LLM evaluation metric
            "BLEU",                             # Sequence-level evaluation
            "Benchmark (computing)",

            # ── Tokenization ─────────────────────────────────────────────────
            "Byte pair encoding",               # Standard LLM tokenization algorithm
            "Tokenization",
            "Large language model",
        ],
    },

    # ── Energy & Climate ──────────────────────────────────────────────────────
    # Power generation technologies, energy economics, carbon metrics, and
    # the energy transition. Covers the canonical "nuclear vs wind vs solar"
    # demo query and related energy policy topics.
    "energy_climate": {
        "description": "Power generation, energy economics, carbon footprint, and the energy transition",
        "wikipedia_titles": [
            # ── Generation technologies ───────────────────────────────────────
            "Nuclear power",
            "Nuclear reactor",
            "Nuclear fusion",
            "Wind power",
            "Offshore wind power",
            "Wind turbine",
            "Solar energy",
            "Solar panel",
            "Photovoltaic system",
            "Concentrated solar power",
            "Hydropower",
            "Geothermal energy",
            "Natural gas",
            "Coal power station",
            "Combined cycle power plant",

            # ── Energy economics & metrics ─────────────────────────────────────
            "Levelized cost of energy",         # LCOE — apples-to-apples cost comparison
            "Capacity factor",                  # % of time a plant runs at rated output
            "Energy storage",
            "Battery storage power station",
            "Pumped-storage hydroelectricity",
            "Smart grid",
            "Electricity market",
            "Feed-in tariff",
            "Renewable energy certificate",

            # ── Carbon & climate ──────────────────────────────────────────────
            "Carbon footprint",
            "Greenhouse gas emissions",
            "Carbon capture and storage",
            "Life-cycle assessment",            # Full carbon accounting method
            "Carbon offset",
            "Net zero emissions",
            "Emissions trading",
            "Intergovernmental Panel on Climate Change",

            # ── Energy policy & transition ────────────────────────────────────
            "Energy transition",
            "Renewable energy",
            "Energy poverty",
            "Distributed generation",
            "Electricity generation",
            "Grid parity",                      # When renewables match fossil cost
            "Power purchase agreement",
        ],
    },

    # ── Economics & Society ───────────────────────────────────────────────────
    # Remote work, labour markets, urban economics, real estate, and
    # broader socioeconomic trends post-2020. Covers the "remote work
    # economic impacts" demo query and related policy topics.
    "economics_society": {
        "description": "Remote work, labour markets, urban economics, real estate, and post-pandemic trends",
        "wikipedia_titles": [
            # ── Remote work & labour ──────────────────────────────────────────
            "Remote work",
            "Digital nomad",
            "Telecommuting",
            "Four-day week",
            "Gig economy",
            "Knowledge worker",
            "Labour economics",
            "Unemployment",
            "Wage",
            "Productivity",

            # ── Urban economics & real estate ─────────────────────────────────
            "Urban economics",
            "Real estate economics",
            "Real estate",
            "Commercial property",
            "Residential area",
            "Gentrification",
            "Urban sprawl",
            "Suburbanization",
            "Ghost town",                       # Office vacancy → ghost towns dynamic
            "Commuting",

            # ── Housing markets ───────────────────────────────────────────────
            "Housing market",
            "House price index",
            "Renting",
            "Mortgage",
            "Property tax",

            # ── Macroeconomics & policy ───────────────────────────────────────
            "COVID-19 recession",
            "Great Resignation",
            "Supply and demand",
            "Inflation",
            "Interest rate",
            "Central bank",
            "Fiscal policy",
            "Economic inequality",
        ],
    },
}
