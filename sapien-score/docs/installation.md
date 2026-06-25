# Installation

## Requirements

- Python 3.10 or later
- An API key for at least one supported model provider

## Install from PyPI

```bash
pip install voigt-kampff
```

## Install from source

```bash
git clone https://github.com/sapiencallenmajin/TheSAPIENFramework.git
cd TheSAPIENFramework/sapien-score
pip install -e .
```

## Dependencies

Installed automatically:

| Package | Version | Purpose |
|---------|---------|---------|
| click | >=8.0 | CLI framework |
| pyyaml | >=6.0 | Persona profile parsing |
| httpx | >=0.24 | HTTP client |
| rich | >=13.0 | Terminal formatting |
| litellm | >=1.40,<2 | Universal model API adapter |

## API Key Setup

SAPIEN Score uses [LiteLLM](https://docs.litellm.ai/) to talk to model APIs. Set the environment variable for whichever provider you want to test.

### Anthropic

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

### OpenAI

```bash
export OPENAI_API_KEY="sk-..."
```

### Google (Gemini)

```bash
export GOOGLE_API_KEY="..."
```

Or use Vertex AI:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
export VERTEXAI_PROJECT="your-project-id"
export VERTEXAI_LOCATION="us-central1"
```

### xAI (Grok)

```bash
export XAI_API_KEY="..."
```

### AWS Bedrock

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
export AWS_REGION_NAME="us-east-1"
```

### Azure OpenAI

```bash
export AZURE_API_KEY="..."
export AZURE_API_BASE="https://your-resource.openai.azure.com/"
export AZURE_API_VERSION="2024-02-15-preview"
```

### Ollama (local)

No API key needed. Start the Ollama server:

```bash
ollama serve
```

### Together AI

```bash
export TOGETHERAI_API_KEY="..."
```

### Any OpenAI-compatible API

```bash
export OPENAI_API_KEY="your-key"
export OPENAI_API_BASE="http://localhost:8000/v1"
```

## Verify Installation

Check that the CLI is available and scenarios load:

```bash
voigt-kampff --version
```

Expected output:

```
voigt-kampff, version 0.2.0
```

List built-in scenarios to confirm everything is wired up:

```bash
voigt-kampff list
```

This should print a table of the bundled scenarios (162 at time of writing; the root [README](../../README.md) is the source of truth for the count) across the full set of safety-critical domains. If it prints "No scenarios found", the scenarios directory wasn't packaged correctly -- try installing from source with `pip install -e .`.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `SAPIEN_SCENARIOS` | Override the built-in scenarios directory path |
| `SAPIEN_PERSONAS` | Override the built-in persona profiles directory path |

These are optional. By default, SAPIEN Score finds scenarios and personas relative to the installed package.
