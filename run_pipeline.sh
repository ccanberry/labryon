#!/bin/bash
set -e

# Full AI Agent Testing Pipeline
# This script runs the complete SOP-based testing pipeline for a worker from the database.
#
# Usage: ./run_pipeline.sh --worker "Worker Name" [--parallel 4] [--partition-happy] [--insights]
#
# For more control, use the labryon/run_full_pipeline.sh script directly.

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- Argument Parsing ---
show_help() {
  echo "Usage: $0 --worker <worker-name> [options]"
  echo
  echo "Runs the full SOP-based testing pipeline from database snapshot to AI insights."
  echo
  echo "Required:"
  echo "  --worker <name>         Worker name from database (required)"
  echo
  echo "Options:"
  echo "  --parallel <N>          Number of parallel processes (default: 4)"
  echo "  --partition-happy       Test partition-level happy paths"
  echo "  --partition-unhappy     Test partition-level unhappy paths"
  echo "  --scenario-happy        Test scenario-level happy paths"
  echo "  --scenario-unhappy      Test scenario-level unhappy paths"
  echo "  --unhappy               Test individual unhappy variations"
  echo "  --all                   Test all path types"
  echo "  --insights              Generate AI-powered insights at the end"
  echo "  --threshold <N>         Score threshold for insights (default: 0.7)"
  echo "  --override              Save to worker directory without timestamp"
  echo "  --start <step>          Start from specific step (snapshot|sop|scenarios|simulation|evaluation|report)"
  echo "  --dir <path>            Use specific directory for graph_output.json (e.g., sop/Worker/20251111_1234)"
  echo "  -h, --help              Show this help message"
  echo
  echo "Examples:"
  echo "  $0 --worker 'Customer Service Demo' --partition-happy --insights --parallel 4"
  echo "  $0 --worker 'RFQ Handling' --all --parallel 8 --insights"
  echo "  $0 --worker 'My Worker' --partition-happy --partition-unhappy --parallel 4"
  echo "  $0 --worker 'Customer Service Demo' --start simulation --scenario-happy --parallel 4 --insights"
  echo "  $0 --dir labryon/sop/Customer_Service_Demo/20251111_1234 --start evaluation --scenario-happy --insights"
  echo
  echo "For more details, see: labryon/docs/README.md"
}

WORKER_NAME=""
PASS_THROUGH_ARGS=""
GENERATE_INSIGHTS=false
INSIGHTS_THRESHOLD="0.7"
USE_OVERRIDE=false
START_STEP=""
CUSTOM_DIR=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -w|--worker)
            if [[ -n "$2" && "$2" != --* ]]; then
                WORKER_NAME="$2"
                shift 2
            else
                echo -e "${RED}Error: --worker requires a non-empty argument.${NC}" >&2
                exit 1
            fi
            ;;
        -override|--override)
            USE_OVERRIDE=true
            shift
            ;;
        -insights|--insights)
            GENERATE_INSIGHTS=true
            shift
            ;;
        -threshold|--threshold)
            if [[ -n "$2" && "$2" != --* ]]; then
                INSIGHTS_THRESHOLD="$2"
                shift 2
            else
                echo -e "${RED}Error: --threshold requires a numeric argument.${NC}" >&2
                exit 1
            fi
            ;;
        -s|-start|--start)
            if [[ -n "$2" && "$2" != --* ]]; then
                START_STEP="$2"
                if [ "$START_STEP" == "scenario" ]; then START_STEP="scenarios"; fi
                shift 2
            else
                echo -e "${RED}Error: --start requires a step name.${NC}" >&2
                exit 1
            fi
            ;;
        -dir|--dir)
            if [[ -n "$2" && "$2" != --* ]]; then
                CUSTOM_DIR="$2"
                shift 2
            else
                echo -e "${RED}Error: --dir requires a directory path.${NC}" >&2
                exit 1
            fi
            ;;
        *)
            PASS_THROUGH_ARGS+="$1 "
            shift
            ;;
    esac
done

# Check if worker name is provided (not needed if starting from simulation/evaluation/insights)
if [ -z "$WORKER_NAME" ] && [ -z "$START_STEP" ]; then
    echo -e "${RED}Error: Worker name is required.${NC}" >&2
    show_help
    exit 1
fi

# Extract flags for different scripts (each script has different accepted flags)
# scenario_generator: only accepts --parallel
SCENARIO_GEN_ARGS=""
if [[ "$PASS_THROUGH_ARGS" =~ --parallel[[:space:]]+([0-9]+) ]]; then
    SCENARIO_GEN_ARGS="--parallel ${BASH_REMATCH[1]}"
fi

# simulation_runner & evaluate_results: accept --parallel and test path flags
SIM_EVAL_ARGS=""
for arg in $PASS_THROUGH_ARGS; do
    if [[ "$arg" =~ ^(--parallel|--all|--scenario-happy|--scenario-unhappy|--partition-happy|--partition-unhappy|--happy|--unhappy)$ ]]; then
        SIM_EVAL_ARGS+="$arg "
    elif [[ "$SIM_EVAL_ARGS" =~ --parallel[[:space:]]*$ ]]; then
        # This is the number following --parallel
        SIM_EVAL_ARGS+="$arg "
    fi
done

# aggregate_agent_stats: only uses --report, --insights, --threshold (handled separately)

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║       AI Agent Testing Pipeline - SOP-Based Testing       ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo
if [ -n "$WORKER_NAME" ]; then
    echo -e "${GREEN}Worker:${NC} $WORKER_NAME"
fi
if [ -n "$START_STEP" ]; then
    echo -e "${GREEN}Starting from:${NC} $START_STEP"
fi
if [ -n "$PASS_THROUGH_ARGS" ]; then
    echo -e "${GREEN}Options:${NC} $PASS_THROUGH_ARGS"
fi
echo

# Determine graph path based on custom directory or start step
if [ -n "$CUSTOM_DIR" ]; then
    # Use custom directory if provided (convert to absolute path)
    CUSTOM_DIR_ABS=$(cd "$CUSTOM_DIR" 2>/dev/null && pwd || echo "$CUSTOM_DIR")
    GRAPH_OUTPUT_PATH="$CUSTOM_DIR_ABS/graph_output.json"
    echo -e "${GREEN}Using custom directory: $GRAPH_OUTPUT_PATH${NC}"
    echo
elif [ -n "$START_STEP" ]; then
    # When starting from a later step, find existing graph
    if [ -z "$WORKER_NAME" ]; then
        echo -e "${RED}Error: --worker required when using --start without --dir${NC}" >&2
        exit 1
    fi
    WORKER_SAFE_NAME=$(echo "$WORKER_NAME" | tr ' ' '_' | tr -cd '[:alnum:]_.-')
    if [ "$USE_OVERRIDE" = true ]; then
        GRAPH_OUTPUT_PATH="sop/$WORKER_SAFE_NAME/graph_output.json"
    else
        LATEST_DIR=$(ls -t "sop/$WORKER_SAFE_NAME/" 2>/dev/null | head -1)
        if [ -z "$LATEST_DIR" ]; then
            echo -e "${RED}Error: No existing runs found for worker${NC}" >&2
            exit 1
        fi
        GRAPH_OUTPUT_PATH="sop/$WORKER_SAFE_NAME/$LATEST_DIR/graph_output.json"
    fi
    if [ ! -f "$GRAPH_OUTPUT_PATH" ]; then
        echo -e "${RED}Error: Graph output not found at: $GRAPH_OUTPUT_PATH${NC}" >&2
        exit 1
    fi
    echo -e "${GREEN}Using existing graph: $GRAPH_OUTPUT_PATH${NC}"
    echo
fi

# Step 1: Create snapshot from database (skip if --start provided)
if [ -z "$START_STEP" ] || [ "$START_STEP" = "snapshot" ]; then
echo -e "${BLUE}[1/6] Creating worker snapshot from database...${NC}"
echo "=================================================="

# Build snapshot command with override flag if needed
SNAPSHOT_CMD="python3 src/snapshot.py --worker \"$WORKER_NAME\""
if [ "$USE_OVERRIDE" = true ]; then
    SNAPSHOT_CMD+=" --override"
fi

eval $SNAPSHOT_CMD

if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Failed to create worker snapshot!${NC}" >&2
    exit 1
fi

# Determine the graph output path based on override flag
WORKER_SAFE_NAME=$(echo "$WORKER_NAME" | tr ' ' '_' | tr -cd '[:alnum:]_.-')
if [ "$USE_OVERRIDE" = true ]; then
    GRAPH_OUTPUT_PATH="sop/$WORKER_SAFE_NAME/graph_output.json"
else
    LATEST_DIR=$(ls -t "sop/$WORKER_SAFE_NAME/" | head -1)
    GRAPH_OUTPUT_PATH="sop/$WORKER_SAFE_NAME/$LATEST_DIR/graph_output.json"
fi

if [ ! -f "$GRAPH_OUTPUT_PATH" ]; then
    echo -e "${RED}Error: Graph output not found at: $GRAPH_OUTPUT_PATH${NC}" >&2
    exit 1
fi

echo -e "${GREEN}✓ Snapshot created: $GRAPH_OUTPUT_PATH${NC}"
echo
fi

# Step 2: Analyze SOP and extract workflow
if [[ -z "$START_STEP" ]] || [[ "$START_STEP" =~ ^(snapshot|sop)$ ]]; then
echo -e "${BLUE}[2/6] Analyzing SOP and extracting workflow structure...${NC}"
echo "=================================================="
python3 src/sop_process.py --sop-graph "$GRAPH_OUTPUT_PATH"

if [ $? -ne 0 ]; then
    echo -e "${RED}Error: SOP analysis failed!${NC}" >&2
    exit 1
fi

echo -e "${GREEN}✓ SOP analysis completed${NC}"
echo
fi

# Step 3: Generate test scenarios
if [[ -z "$START_STEP" ]] || [[ "$START_STEP" =~ ^(snapshot|sop|scenarios)$ ]]; then
echo -e "${BLUE}[3/6] Generating test scenarios...${NC}"
echo "=================================================="
python3 src/scenario_generator.py --sop-graph "$GRAPH_OUTPUT_PATH" $SCENARIO_GEN_ARGS

if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Scenario generation failed!${NC}" >&2
    exit 1
fi

echo -e "${GREEN}✓ Test scenarios generated${NC}"
echo
fi

# Step 4: Run simulations with mocked tools
if [[ -z "$START_STEP" ]] || [[ "$START_STEP" =~ ^(snapshot|sop|scenarios|simulation)$ ]]; then
echo -e "${BLUE}[4/6] Running simulations (with mocked tools and tracing)...${NC}"
echo "=================================================="
python3 src/simulation_runner.py --sop-graph "$GRAPH_OUTPUT_PATH" $SIM_EVAL_ARGS

if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Simulation failed!${NC}" >&2
    exit 1
fi

echo -e "${GREEN}✓ Simulations completed${NC}"
echo
fi

# Step 5: Evaluate results
if [[ -z "$START_STEP" ]] || [[ "$START_STEP" =~ ^(snapshot|sop|scenarios|simulation|evaluation)$ ]]; then
echo -e "${BLUE}[5/6] Evaluating agent performance...${NC}"
echo "=================================================="
python3 src/evaluate_results.py --sop-graph "$GRAPH_OUTPUT_PATH" $SIM_EVAL_ARGS

if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Evaluation failed!${NC}" >&2
    exit 1
fi

echo -e "${GREEN}✓ Evaluation completed${NC}"
echo
fi

# Step 6: Generate insights (optional)
if [ "$GENERATE_INSIGHTS" = true ]; then
    echo -e "${BLUE}[6/6] Generating AI-powered insights and recommendations...${NC}"
    echo "=================================================="
    python3 src/aggregate_agent_stats.py "$GRAPH_OUTPUT_PATH" --insights --report --threshold "$INSIGHTS_THRESHOLD"

    if [ $? -ne 0 ]; then
        echo -e "${YELLOW}Warning: Insights generation failed (check GEMINI_API_KEY)${NC}" >&2
    else
        echo -e "${GREEN}✓ Insights generated${NC}"
    fi
    echo
else
    echo -e "${YELLOW}[6/6] Skipping insights generation (use --insights to enable)${NC}"
    echo
fi

# Final summary
STATS_PATH="${GRAPH_OUTPUT_PATH%.json}"
echo "=================================================="
echo -e "${GREEN}✓ Pipeline completed successfully!${NC}"
echo "=================================================="
echo -e "${BLUE}Results:${NC}"
echo "  • Full test data:     $GRAPH_OUTPUT_PATH"
if [ "$GENERATE_INSIGHTS" = true ]; then
    echo "  • Statistics:         ${STATS_PATH}_stats.json"
    echo "  • Summary report:     ${STATS_PATH}_stats_report.txt"
    echo "  • AI insights:        ${STATS_PATH}_stats_insights.md"
fi
echo
echo -e "${BLUE}Next steps:${NC}"
echo "  • Review the AI insights for improvement recommendations"
echo "  • Check agent_stats_report.txt for quick performance overview"
echo "  • Examine low-scoring scenarios in graph_output.json"
echo