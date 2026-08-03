# Labryon Installation Guide

Complete setup guide for the Labryon AI Agent Testing Framework.

## Table of Contents
- [System Requirements](#system-requirements)
- [Installation Steps](#installation-steps)
- [Environment Configuration](#environment-configuration)
- [Verification](#verification)
- [Troubleshooting](#troubleshooting)

## System Requirements

### Required
- **Python**: 3.11 or higher
- **Docker**: For PostgreSQL and Redis
- **Git**: For cloning/updating repository

### No System Dependencies Required
Unlike typical Python projects, Labryon doesn't require any system-level libraries (like libmagic or libpq-dev). All dependencies are pure Python packages.

### Operating Systems
- Linux (Ubuntu 20.04+, Debian 11+)
- macOS (10.15+)
- Windows (via WSL2)

## Installation Steps

### Step 1: Install Python 3.11+

Ensure Python 3.11 or higher is installed:

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv
```

**macOS:**
```bash
brew install python@3.11
```

**Windows (WSL2):**
```bash
# Use Ubuntu instructions within WSL2
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv
```

**Verify installation:**
```bash
python3 --version  # Should be 3.11 or higher
```

### Step 2: Navigate to Labryon Directory

```bash
cd labryon
```

### Step 3: Create Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate  # Linux/macOS
# Or on Windows (WSL2): source venv/bin/activate
```

**Verify activation:**
```bash
which python  # Should show: .../labryon/venv/bin/python
python --version  # Should be 3.11+
```

### Step 4: Upgrade pip

```bash
pip install --upgrade pip setuptools wheel
```

### Step 5: Install Python Dependencies

```bash
pip install -r requirements.txt
```

**Expected packages (8 total):**
- Core: python-dotenv, pyyaml
- Database: sqlalchemy, psycopg2-binary
- Validation: pydantic
- AI/LLM: agno (with anthropic, google, openai)
- Evaluation: composo, tenacity

### Step 6: Start Infrastructure Services

Start database and redis using docker-compose from project root:

```bash
# Note: docker-compose.yml is in the project root, so we need to go there
cd /path/to/project-root
docker-compose up -d db redis

# Verify services are running
docker-compose ps
```

**Note:** Only docker-compose needs to run from project root. All labryon scripts run from `labryon/` directory and use their own `.env` file at `labryon/.env`.

**Expected output:**
```
NAME          STATUS    PORTS
postgres-db   Up        0.0.0.0:5432->5432/tcp
redis         Up        0.0.0.0:6379->6379/tcp
```

## Environment Configuration

### Create .env File

Create `.env` in the **labryon directory** (`labryon/.env`):

```bash
# Copy the example file
cd labryon
cp .env.example .env

# Edit with your actual credentials
nano .env
```

**Required configuration:**

```bash
###############################
# Database Configuration
###############################
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=workflow_db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=your_secure_password

###############################
# LLM API Keys
###############################
# Anthropic Claude
ANTHROPIC_API_KEY=sk-ant-api03-...

# Google Gemini
GEMINI_API_KEY=AIzaSyD...

# OpenAI (optional)
# OPENAI_API_KEY=sk-...

###############################
# Evaluation & Tracing
###############################
# Composo (optional but recommended for runtime tracing)
COMPOSO_API_KEY=...

###############################
# Observability (optional)
###############################
# LANGFUSE_PUBLIC_KEY=...
# LANGFUSE_SECRET_KEY=...
```

### Environment Variables Details

**Required for basic functionality:**
- `POSTGRES_*`: Database connection for fetching worker configurations
- `ANTHROPIC_API_KEY` or `GEMINI_API_KEY`: At least one LLM provider for scenario generation

**Optional but recommended:**
- `COMPOSO_API_KEY`: Enables runtime tracing during simulations

## Verification

### 1. Check Python Environment

```bash
cd labryon
source venv/bin/activate

python -c "
import sys
import sqlalchemy
import pydantic
import yaml
import agno
import composo
print(f'✓ Python {sys.version}')
print(f'✓ SQLAlchemy {sqlalchemy.__version__}')
print(f'✓ Pydantic {pydantic.__version__}')
print(f'✓ Agno {agno.__version__}')
print(f'✓ Composo {composo.__version__}')
print('✓ All core packages installed successfully!')
"
```

### 2. Check Infrastructure Services

```bash
cd /path/to/project-root

# Check database connection
docker-compose exec db psql -U postgres -d workflow_db -c "SELECT version();"

# Check redis
docker-compose exec redis redis-cli ping
# Should return: PONG
```

### 3. Run a Test Pipeline

```bash
# Activate venv and stay in labryon directory
cd labryon
source venv/bin/activate

# List available workers (without running tests)
python src/snapshot.py

# If workers are listed successfully, your setup is working!
```

## Troubleshooting

### Issue: "psycopg2-binary installation failed"

**Solution:**
```bash
# This should NOT happen with psycopg2-binary (it's pre-compiled)
# If it does, upgrade pip first:
pip install --upgrade pip setuptools wheel

# Then try again:
pip install --force-reinstall psycopg2-binary
```

### Issue: "No module named 'agno'"

**Solution:**
```bash
# Make sure you're in the virtual environment
source venv/bin/activate

# Reinstall agno with all extras
pip install --force-reinstall agno[anthropic,google,openai]==2.2.6
```

### Issue: "Could not connect to database"

**Solution:**
```bash
# Check if database is running
docker-compose ps

# Restart database
docker-compose restart db

# Check .env file has correct credentials
cat .env | grep POSTGRES

# Test connection directly
docker-compose exec db psql -U postgres -d workflow_db
```

### Issue: "ANTHROPIC_API_KEY not set"

**Solution:**
```bash
# Check .env file exists in project root
ls -la .env

# Verify API key is set
cat .env | grep ANTHROPIC_API_KEY

# If missing, add it:
echo "ANTHROPIC_API_KEY=sk-ant-..." >> .env
```

### Issue: Virtual environment not activating

**Solution:**
```bash
# Recreate virtual environment
cd labryon
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## Next Steps

Once installation is complete:

1. **Read the Quick Start**: See `docs/README.md` for usage examples
2. **Run the Pipeline**: Try `./labryon/run_pipeline.sh --worker "Worker Name" --partition-happy`
3. **Review Documentation**: Check `docs/CLAUDE.md` for technical details

## Getting Help

- **Documentation**: `labryon/docs/`
- **Issues**: Create an issue in the repository
- **Quick Reference**: See `docs/TLDR.md`
