# Hermes-Trader Skills

Skills for Hermes Agent integration. Drop these into your `~/.hermes/skills/` directory
or symlink them for immediate use.

## Available Skills

| Skill | Purpose |
|-------|---------|
| `hermes-trader-agent` | Full trading agent architecture, config, pitfalls |

## Installation

```bash
# Option 1: Symlink (recommended for dev)
ln -s /path/to/hermes-trader/skills/hermes-trader-agent ~/.hermes/skills/hermes-trader-agent

# Option 2: Copy
cp -r /path/to/hermes-trader/skills/hermes-trader-agent ~/.hermes/skills/
```

## Usage in Hermes Agent

```
skill_view(name='hermes-trader-agent')
```
