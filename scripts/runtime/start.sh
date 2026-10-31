#!/bin/bash

# AutoClip one-click startup script
# Version: 2.0
# Function: Start the complete AutoClip system (backend API + Celery Worker + frontend UI)

set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
STARTED_ROLES=()
DETACH=false
STARTUP_COMPLETE=false
case "${1:-}" in
    --detach) DETACH=true ;;
    --help|-h) echo 'Usage: ./start_autoclip.sh [--detach]'; exit 0 ;;
    '') ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
esac
manage_service() { bash "$ROOT/scripts/runtime/local.sh" "$@"; }
# Parse .env as dotenv data (env.example contains an unquoted logging format).
# Re-exec once so all child services inherit the same configuration.
if [[ "${_AUTOCLIP_ENV_LOADED:-}" != 1 && -x venv/bin/python ]]; then
    export _AUTOCLIP_ENV_LOADED=1
    exec venv/bin/python -c 'import os, sys; from dotenv import load_dotenv; load_dotenv(".env", override=False); os.execvpe("bash", ["bash", sys.argv[1], *sys.argv[2:]], os.environ)' "${BASH_SOURCE[0]}" "$@"
fi


# =============================================================================
# Configuration area
# =============================================================================

# Service port configuration
BACKEND_PORT=${BACKEND_PORT:-8000}
FRONTEND_PORT=${FRONTEND_PORT:-3000}

# Service timeout configuration
BACKEND_STARTUP_TIMEOUT=60
FRONTEND_STARTUP_TIMEOUT=90
HEALTH_CHECK_TIMEOUT=10

# Log configuration
LOG_DIR="logs"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
CELERY_LOG="$LOG_DIR/celery.log"

# PID files
BACKEND_PID_FILE="backend.pid"
FRONTEND_PID_FILE="frontend.pid"
CELERY_PID_FILE="celery.pid"

# =============================================================================
# Color and style definitions
# =============================================================================

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

# Icon definitions
ICON_SUCCESS="✅"
ICON_ERROR="❌"
ICON_WARNING="⚠️"
ICON_INFO="ℹ️"
ICON_ROCKET="🚀"
ICON_GEAR="⚙️"
ICON_DATABASE="🗄️"
ICON_WORKER="👷"
ICON_WEB="🌐"
ICON_HEALTH="💚"

# =============================================================================
# Utility functions
# =============================================================================

log_info() {
    echo -e "${BLUE}${ICON_INFO} $1${NC}"
}

log_success() {
    echo -e "${GREEN}${ICON_SUCCESS} $1${NC}"
}

log_warning() {
    echo -e "${YELLOW}${ICON_WARNING} $1${NC}"
}

log_error() {
    echo -e "${RED}${ICON_ERROR} $1${NC}"
}

log_header() {
    echo -e "\n${PURPLE}${ICON_ROCKET} $1${NC}"
    echo -e "${PURPLE}$(printf '=%.0s' {1..50})${NC}"
}

log_step() {
    echo -e "\n${CYAN}${ICON_GEAR} $1${NC}"
}

# Check whether a command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Check whether a port is in use
port_in_use() {
    lsof -i ":$1" >/dev/null 2>&1
}

# Wait for a service to start
wait_for_service() {
    local url="$1"
    local timeout="$2"
    local service_name="$3"
    
    log_info "Waiting for $service_name to start..."
    
    for i in $(seq 1 "$timeout"); do
        if curl -fsS "$url" >/dev/null 2>&1; then
            log_success "$service_name has started"
            return 0
        fi
        sleep 1
    done
    
    log_error "$service_name startup timed out"
    return 1
}

# =============================================================================
# Environment check functions
# =============================================================================

check_environment() {
    log_header "Environment check"
    
    # Check operating system
    if [[ "$OSTYPE" == "darwin"* ]]; then
        log_success "macOS system detected"
    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        log_success "Linux system detected"
    else
        log_warning "Unrecognized operating system: $OSTYPE"
    fi
    
    # Check required commands (skip node check in Docker environment)
    if [[ "${ENVIRONMENT:-}" != "production" ]] || [[ ! -f "/.dockerenv" ]]; then
        local required_commands=("python3" "node" "npm" "curl" "lsof")
        for cmd in "${required_commands[@]}"; do
            if command_exists "$cmd"; then
                log_success "$cmd is installed"
            else
                log_error "$cmd is not installed, please install it first"
                exit 1
            fi
        done
    else
        log_info "Docker environment detected, skipping node environment check"
        local required_commands=("python3" "curl" "lsof")
        for cmd in "${required_commands[@]}"; do
            if command_exists "$cmd"; then
                log_success "$cmd is installed"
            else
                log_error "$cmd is not installed, please install it first"
                exit 1
            fi
        done
    fi
    
    # Check Python version
    local python_version=$(python3 --version 2>&1 | cut -d' ' -f2)
    log_info "Python version: $python_version"
    
    # Check Node.js version
    local node_version=$(node --version)
    log_info "Node.js version: $node_version"
    
    # Check virtual environment
    if [[ ! -d "venv" ]]; then
        log_error "Virtual environment does not exist, please create it first: python3 -m venv venv"
        exit 1
    fi
    log_success "Virtual environment exists"
    
    # Check project structure
    mkdir -p data
    local required_dirs=("backend" "frontend")
    for dir in "${required_dirs[@]}"; do
        if [[ -d "$dir" ]]; then
            log_success "Directory $dir exists"
        else
            log_error "Directory $dir does not exist"
            exit 1
        fi
    done
}

# =============================================================================
# Service startup functions
# =============================================================================

start_redis() {
    log_step "Starting Redis service"
    
    if manage_service redis >/dev/null 2>&1; then
        log_success "Redis service is already running"
        return 0
    fi
    
    if [[ "${REDIS_URL:-redis://localhost:6379/0}" != 'redis://localhost:6379/0' ]]; then
        log_error "The configured Redis is unavailable, please check REDIS_URL; local Redis will not be started to replace it"
        exit 1
    fi
    log_info "Starting Redis service..."
    
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if command_exists brew; then
            brew services start redis
            sleep 3
        else
            log_error "Please start the Redis service manually"
            exit 1
        fi
    else
        systemctl start redis-server 2>/dev/null || service redis-server start 2>/dev/null || {
            log_error "Unable to start Redis service, please start it manually"
            exit 1
        }
    fi
    
    if manage_service redis >/dev/null 2>&1; then
        log_success "Redis service started successfully"
    else
        log_error "Redis service failed to start"
        exit 1
    fi
}

setup_environment() {
    log_step "Setting up environment"
    
    # Create log directory
    mkdir -p "$LOG_DIR"
    
    # Activate virtual environment
    log_info "Activating virtual environment..."
    source venv/bin/activate
    
    # Set Python path
    : "${PYTHONPATH:=}"
    export PYTHONPATH="${PWD}:${PYTHONPATH}"
    log_info "Setting Python path: $PYTHONPATH"
    
    if [[ ! -f .env ]]; then
        cp env.example .env
        log_info "Created .env; please edit the model configuration as needed"
    fi

    # Check Python dependencies
    log_info "Checking Python dependencies..."
    if ! python -c "import fastapi, celery, sqlalchemy, psutil, dotenv, redis" 2>/dev/null; then
        log_error "Missing dependencies, please run first: python -m pip install -r requirements.txt"
        exit 1
    fi
    log_success "Python dependency check complete"
}

init_database() {
    log_step "Initializing database"
    
    # Ensure data directory exists
    mkdir -p data
    
    python init_database.py
    log_success "Database initialization successful"
}

start_celery() {
    log_step "Starting Celery Worker"
    
    log_info "Starting Celery Worker..."
    nohup celery -A backend.core.celery_app worker \
        --loglevel=info \
        --concurrency=1 \
        --prefetch-multiplier=1 \
        -Q celery,processing,video,notification,upload \
        --hostname=worker@%h \
        > "$CELERY_LOG" 2>&1 &
    
    local celery_pid=$!
    STARTED_ROLES+=(celery)
    manage_service record celery "$celery_pid"
    
    # Wait for Worker to start
    sleep 5
    
    if manage_service check celery >/dev/null; then
        log_success "Celery Worker has started (PID: $celery_pid)"
    else
        log_error "Celery Worker failed to start"
        log_info "View log: tail -f $CELERY_LOG"
        exit 1
    fi
}

start_backend() {
    log_step "Starting backend API service"
    
    if port_in_use "$BACKEND_PORT"; then
        log_error "Port $BACKEND_PORT is already in use; please confirm which service owns it first, other processes will not be terminated"
        exit 1
    fi


    log_info "Starting backend service (port: $BACKEND_PORT)..."
    nohup python -m uvicorn backend.main:app \
        --host 0.0.0.0 \
        --port "$BACKEND_PORT" \
        --reload \
        --reload-dir backend \
        --reload-include '*.py' \
        --reload-exclude 'data/*' \
        --reload-exclude 'logs/*' \
        --reload-exclude 'uploads/*' \
        --reload-exclude '*.log' \
        > "$BACKEND_LOG" 2>&1 &
    
    local backend_pid=$!
    STARTED_ROLES+=(backend)
    manage_service record backend "$backend_pid"
    
    # Wait for backend to start
    if wait_for_service "http://localhost:$BACKEND_PORT/api/v1/health/" "$BACKEND_STARTUP_TIMEOUT" "Backend service" && manage_service check backend; then
        log_success "Backend service has started (PID: $backend_pid)"
    else
        log_error "Backend service failed to start"
        log_info "View log: tail -f $BACKEND_LOG"
        exit 1
    fi
}

start_frontend() {
    log_step "Starting frontend service"
    
    if port_in_use "$FRONTEND_PORT"; then
        log_error "Port $FRONTEND_PORT is already in use; please confirm which service owns it first, other processes will not be terminated"
        exit 1
    fi

    # Enter frontend directory
    cd frontend || {
        log_error "Unable to enter frontend directory"
        exit 1
    }
    
    # Check frontend dependencies
    if [[ ! -d "node_modules" ]]; then
        log_info "Installing frontend dependencies..."
        npm ci
    fi
    
    log_info "Starting frontend service (port: $FRONTEND_PORT)..."
    nohup npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT" --strictPort \
        > "../$FRONTEND_LOG" 2>&1 &
    
    local frontend_pid=$!
    STARTED_ROLES+=(frontend)
    cd "$ROOT"
    manage_service record frontend "$frontend_pid"
    
    # Returned to project root to record process ownership.
    
    # Wait for frontend to start
    if wait_for_service "http://localhost:$FRONTEND_PORT/" "$FRONTEND_STARTUP_TIMEOUT" "Frontend service" && manage_service check frontend; then
        log_success "Frontend service has started (PID: $frontend_pid)"
    else
        log_error "Frontend service failed to start"
        log_info "View log: tail -f $FRONTEND_LOG"
        exit 1
    fi
}

# =============================================================================
# Health check functions
# =============================================================================

health_check() {
    log_header "System health check"
    
    local all_healthy=true
    
    # Check backend
    log_info "Checking backend service..."
    if curl -fsS "http://localhost:$BACKEND_PORT/api/v1/health/" >/dev/null 2>&1; then
        log_success "Backend service healthy"
    else
        log_error "Backend service unhealthy"
        all_healthy=false
    fi
    
    # Check frontend
    log_info "Checking frontend service..."
    if curl -fsS "http://localhost:$FRONTEND_PORT/" >/dev/null 2>&1; then
        log_success "Frontend service healthy"
    else
        log_error "Frontend service unhealthy"
        all_healthy=false
    fi
    
    # Check Redis
    log_info "Checking Redis service..."
    if manage_service redis >/dev/null 2>&1; then
        log_success "Redis service healthy"
    else
        log_error "Redis service unhealthy"
        all_healthy=false
    fi
    
    # Check Celery Worker
    log_info "Checking Celery Worker..."
    if manage_service check celery >/dev/null; then
        log_success "Celery Worker healthy"
    else
        log_error "Celery Worker unhealthy"
        all_healthy=false
    fi
    
    if [[ "$all_healthy" == true ]]; then
        log_success "All service health checks passed"
        return 0
    else
        log_error "Some service health checks failed"
        return 1
    fi
}

# =============================================================================
# Cleanup functions
# =============================================================================

cleanup() {
    if [[ "$DETACH" == true && "$STARTUP_COMPLETE" == true ]]; then return; fi
    cd "$ROOT"
    if [[ ${#STARTED_ROLES[@]} -gt 0 ]]; then
        for role in "${STARTED_ROLES[@]}"; do manage_service stop "$role" || true; done
    fi
}

# =============================================================================
# Display system information
# =============================================================================

show_system_info() {
    log_header "System startup complete"
    
    echo -e "${WHITE}🎉 AutoClip system has started successfully!${NC}"
    echo ""
    echo -e "${CYAN}📊 Service status:${NC}"
    echo -e "  ${ICON_WEB} Backend API:     http://localhost:$BACKEND_PORT"
    echo -e "  ${ICON_WEB} Frontend UI:     http://localhost:$FRONTEND_PORT"
    echo -e "  ${ICON_WEB} API docs:        http://localhost:$BACKEND_PORT/docs"
    echo -e "  ${ICON_HEALTH} Health check:   http://localhost:$BACKEND_PORT/api/v1/health/"
    echo ""
    echo -e "${CYAN}📝 Log files:${NC}"
    echo -e "  Backend log: tail -f $BACKEND_LOG"
    echo -e "  Frontend log: tail -f $FRONTEND_LOG"
    echo -e "  Celery log: tail -f $CELERY_LOG"
    echo ""
    echo -e "${CYAN}🛑 Stop the system:${NC}"
    echo -e "  ./stop_autoclip.sh or press Ctrl+C"
    echo ""
    echo -e "${YELLOW}💡 Usage instructions:${NC}"
    echo -e "  1. Visit http://localhost:$FRONTEND_PORT to use the frontend UI"
    echo -e "  2. Upload a video file or enter a Bilibili link"
    echo -e "  3. The system will automatically start the AI processing pipeline"
    echo -e "  4. View processing progress and results in real time"
    echo ""
}

# =============================================================================
# Signal handling
# =============================================================================

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# =============================================================================
# Main function
# =============================================================================

main() {
    log_header "AutoClip System Launcher v2.0"
    
    # Environment check
    check_environment
    
    # Start services
    setup_environment
    # Refuse duplicate starts instead of overwriting PID records or killing other apps.
    for role in backend frontend celery; do
        if [[ -f "$role.pid" ]]; then
            if manage_service check "$role"; then
                log_error "$role is already running, please run ./stop_autoclip.sh first"
                exit 1
            fi
            # A stale record is harmless; a live foreign PID is refused by stop.
            manage_service stop "$role"
        fi
    done
    start_redis
    init_database
    start_celery
    start_backend
    start_frontend
    
    # Health check
    if health_check; then
        STARTUP_COMPLETE=true
        show_system_info
        if [[ "$DETACH" == true ]]; then return; fi
        
        # Keep the script running (no loop check)
        log_info "System running... press Ctrl+C to stop"
        log_info "To check system status, run: ./status_autoclip.sh"
        while true; do
            sleep 3600  # Check once per hour to reduce frequency
        done
    else
        log_error "System startup failed, please check the logs"
        exit 1
    fi
}

# Run the main function
main "$@"
