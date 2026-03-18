# claude-collaborator

Multi-AI MCP server for C# codebases. Claude + GLM working together.

## Features

- **Persistent Memory**: Never re-explain your architecture across sessions
- **Code Analysis**: Explore any C# project instantly
- **GLM Integration**: Offload research to another AI model
- **Auto-Detection**: Finds your codebase automatically
- **Zero Config**: Works with just `cwd` setting in Claude Desktop

## Installation

```bash
pip install claude-collaborator
```

Or install from source:
```bash
git clone https://github.com/coreye/claude-collaborator-mcp.git
cd claude-collaborator-mcp
pip install -e .
```

## Quick Start

### Simplest Configuration (Recommended)

Just add to Claude Desktop config using the `cwd` field:

**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "csharp": {
      "command": "claude-collaborator",
      "cwd": "C:\\path\\to\\your\\csharp\\project"
    }
  }
}
```

That's it! The server auto-detects your codebase from the `cwd` directory.

### With Config File

Create `.claude/config.json` in your project:

```json
{
  "codebase_path": ".",
  "glm_api_key": "your_key_here"
}
```

Then in Claude Desktop:
```json
{
  "mcpServers": {
    "csharp": {
      "command": "claude-collaborator",
      "cwd": "C:\\path\\to\\your\\csharp\\project"
    }
  }
}
```

### With Environment Variables (Alternative)

```json
{
  "mcpServers": {
    "csharp": {
      "command": "claude-collaborator",
      "env": {
        "CSHARP_CODEBASE_PATH": "C:\\path\\to\\your\\project"
      }
    }
  }
}
```

## Available Tools

- `explore_project` - Analyze a C# project
- `analyze_architecture` - Get overview of all projects
- `memory_save` - Save findings for future sessions
- `memory_search` - Search saved knowledge
- `glm_explore` - Ask GLM questions (requires API key)

## Configuration

The server looks for configuration in this priority order:

1. **Environment variables** (highest priority)
2. **Project config files** (searched upward):
   - `.claude/config.json` (recommended)
   - `.claude-collaborator.json` (legacy)
3. **Home config**: `~/.claude-collaborator/config.json`
4. **Auto-detection**: Finds `.sln` or `.git` automatically

### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `codebase_path` | auto-detected | Path to C# solution |
| `glm_api_key` | (none) | GLM API key |
| `glm_model` | `glm-4.7` | GLM model to use |
| `memory_path` | `.codebase-memory` | Memory storage path |

### Environment Variables

| Variable | Description |
|----------|-------------|
| `CSHARP_CODEBASE_PATH` | Path to your C# solution |
| `GLM_API_KEY` | GLM API key |
| `GLM_MODEL` | GLM model |
| `MEMORY_PATH` | Memory storage path |

## Auto-Detection

When no path is configured, the server automatically searches upward from the current directory for:
- A `.sln` file (Visual Studio Solution)
- A `.git` directory (Git repository root)

This means **zero configuration** when running from within your project!

## Multiple Projects

Configure separate servers for different codebases:

```json
{
  "mcpServers": {
    "csharp-main": {
      "command": "claude-collaborator",
      "cwd": "C:\\Projects\\MainApp"
    },
    "csharp-tools": {
      "command": "claude-collaborator",
      "cwd": "C:\\Projects\\Tools"
    }
  }
}
```

## GLM Integration (Optional)

GLM provides additional AI-powered code exploration:

```bash
# Install GLM dependencies
pip install claude-collaborator[glm]

# Set your API key (in .claude/config.json or environment)
export GLM_API_KEY=your_api_key_here
```

### Available GLM Models

- `glm-4.7` - Latest model (default)
- `glm-4-flash` - Faster responses
- `glm-4-plus` - Enhanced capabilities

## Development

```bash
# Install in development mode
pip install -e ".[glm]"

# Run tests
py -m unittest tests.test_analyzer

# Format code
black src/
```

## Documentation

See [docs/configuration.md](docs/configuration.md) for detailed configuration options.

## License

MIT License - see [LICENSE](LICENSE) for details.
