"""Fetch the latest vLLM release tag from GitHub and print the version number."""
import json
import urllib.request

with urllib.request.urlopen(
    "https://api.github.com/repos/vllm-project/vllm/releases/latest",
    timeout=30,
) as response:
    tag = json.load(response)["tag_name"]

print(tag.lstrip("v"))
