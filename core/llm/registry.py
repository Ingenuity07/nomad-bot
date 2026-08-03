MODEL_REGISTRY = {
    "qwen3-8b": {
        "name": "qwen-4-8b-it", # default model identifier for small model
        "planning": 4,
        "vision": False,
        "tools": 5,
        "speed": 9,
        "context_limit": 8192,
    },
    "qwen3-14b": {
        "name": "qwen-4-14b-it", # default model identifier for medium model
        "planning": 7,
        "vision": False,
        "tools": 8,
        "speed": 7,
        "context_limit": 16384,
    },
    "gemini-flash": {
        "name": "gemini-2.5-flash", # default model identifier for critical model
        "planning": 10,
        "vision": True,
        "tools": 10,
        "speed": 8,
        "context_limit": 1048576,
    }
}
