#!/bin/bash
set -e
cd /opt/rag-saas

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

case "$1" in
    start)
        echo -e "${GREEN}Starting all Docker services...${NC}"
        docker compose up -d
        echo ""
        docker compose ps
        ;;
    stop)
        echo -e "${YELLOW}Stopping Docker services...${NC}"
        docker compose down
        ;;
    restart)
        echo -e "${YELLOW}Restarting ${2:-all services}...${NC}"
        if [ -n "$2" ]; then
            docker compose restart "$2"
        else
            docker compose restart
        fi
        ;;
    logs)
        docker compose logs -f ${2:-}
        ;;
    status)
        echo -e "${CYAN}=== Docker Services ===${NC}"
        docker compose ps
        echo ""
        echo -e "${CYAN}=== PostgreSQL ===${NC}"
        systemctl status postgresql --no-pager -l | head -5
        echo ""
        echo -e "${CYAN}=== Resource Usage ===${NC}"
        docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" 2>/dev/null || true
        echo ""
        echo -e "${CYAN}=== Disk Usage ===${NC}"
        df -h / /data 2>/dev/null | tail -2
        echo ""
        echo -e "${CYAN}=== Memory ===${NC}"
        free -h
        ;;
    health)
        echo -e "${CYAN}=== Health Checks ===${NC}"
        echo -n "PostgreSQL: "
        pg_isready -h 127.0.0.1 -p 5432 && echo "OK" || echo "FAILED"
        echo -n "Redis: "
        docker compose exec -T redis redis-cli ping 2>/dev/null || echo "FAILED"
        echo -n "MinIO: "
        curl -s http://127.0.0.1:9000/minio/health/live && echo " OK" || echo "FAILED"
        echo -n "Docling: "
        curl -s http://127.0.0.1:5001/health | jq -r '.status' 2>/dev/null || echo "starting..."
        echo -n "LiteLLM: "
        curl -s http://127.0.0.1:4000/health | jq -r '.status' 2>/dev/null || echo "starting..."
        ;;
    pull)
        echo -e "${GREEN}Pulling latest images...${NC}"
        docker compose pull
        ;;
    update)
        echo -e "${GREEN}Updating services...${NC}"
        docker compose pull
        docker compose up -d
        docker image prune -f
        ;;
    psql)
        echo -e "${GREEN}Connecting to PostgreSQL...${NC}"
        source .env
        PGPASSWORD="${PG_ADMIN_PASS:-}" psql -h 127.0.0.1 -U rag_admin -d rag_saas
        ;;
    redis-cli)
        docker compose exec redis redis-cli
        ;;
    minio-console)
        echo -e "${GREEN}MinIO Console available at: http://127.0.0.1:9001${NC}"
        echo "Use SSH tunnel: ssh -L 9001:127.0.0.1:9001 root@$(hostname -I | awk '{print $1}')"
        ;;
    init-minio)
        echo -e "${GREEN}Initializing MinIO bucket...${NC}"
        source .env
        docker run --rm --network host \
            -e MC_HOST_minio="http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@127.0.0.1:9000" \
            minio/mc mb minio/${MINIO_BUCKET} --ignore-existing
        echo -e "${GREEN}Bucket '${MINIO_BUCKET}' created${NC}"
        ;;
    backup)
        echo -e "${GREEN}Creating backup...${NC}"
        TIMESTAMP=$(date +%Y%m%d-%H%M%S)
        mkdir -p /data/backups
        echo "Backing up PostgreSQL..."
        sudo -u postgres pg_dump rag_saas > /data/backups/postgres-$TIMESTAMP.sql
        echo "Backing up Redis..."
        docker compose exec -T redis redis-cli BGSAVE
        sleep 2
        cp /data/redis/dump.rdb /data/backups/redis-$TIMESTAMP.rdb 2>/dev/null || true
        echo -e "${GREEN}Backups saved to /data/backups/${NC}"
        ls -la /data/backups/
        ;;
    clean)
        echo -e "${RED}WARNING: This removes all Docker containers and volumes!${NC}"
        read -p "Type 'yes' to confirm: " confirm
        if [ "$confirm" = "yes" ]; then
            docker compose down -v
            docker system prune -af
            echo -e "${GREEN}Cleanup complete${NC}"
        fi
        ;;
    *)
        echo "RAG SaaS Management"
        echo ""
        echo "Usage: $0 <command>"
        echo ""
        echo "Commands:"
        echo "  start        Start all Docker services"
        echo "  stop         Stop all Docker services"
        echo "  restart      Restart all or specific service"
        echo "  logs         View logs (all or specific service)"
        echo "  status       Show all service status"
        echo "  health       Health check all services"
        echo "  pull         Pull latest Docker images"
        echo "  update       Pull and restart services"
        echo "  psql         Connect to PostgreSQL"
        echo "  redis-cli    Open Redis CLI"
        echo "  minio-console  Info about MinIO console"
        echo "  init-minio   Create MinIO bucket"
        echo "  backup       Backup PostgreSQL and Redis"
        echo "  clean        Remove all containers (DANGEROUS)"
        exit 1
        ;;
esac
