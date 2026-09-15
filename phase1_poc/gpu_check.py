"""Executed on the Colab VM via `colab exec`. Prints GPU identity as JSON."""
import json
import platform
import subprocess

result = {"python_version": platform.python_version()}

try:
    import torch

    result["torch_version"] = torch.__version__
    result["cuda_available"] = torch.cuda.is_available()
    if torch.cuda.is_available():
        result["gpu_name"] = torch.cuda.get_device_name(0)
        result["gpu_count"] = torch.cuda.device_count()
        props = torch.cuda.get_device_properties(0)
        result["gpu_total_memory_gb"] = round(props.total_memory / (1024**3), 2)
except Exception as e:  # pragma: no cover - diagnostic path
    result["torch_error"] = str(e)

try:
    smi = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
         "--format=csv,noheader"],
        capture_output=True, text=True, timeout=15,
    )
    result["nvidia_smi"] = smi.stdout.strip() or smi.stderr.strip()
except Exception as e:  # pragma: no cover - diagnostic path
    result["nvidia_smi_error"] = str(e)

print("GPU_CHECK_RESULT_JSON:" + json.dumps(result))
