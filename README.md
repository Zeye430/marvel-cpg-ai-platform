# Marvel CPG AI Commercial Execution Platform

Course project for CDA650. The team will design and build an AI-powered commercial execution platform for the fictional company Marvel CPG.

## Project scope

The final application will integrate four modules:

1. Commercial execution analytics dashboard
2. Seven-day out-of-stock risk prediction and store visit prioritization
3. Commercial negotiation and price scenario simulator
4. Conversational assistant with Text-to-SQL and retrieval-augmented generation

## Module 1 (Commercial Execution Analytics Dashboard)
### Docker Quick Start Guide
Ensure you have **Docker** and **Docker Compose** installed on your system:

```bash
docker --version
docker compose version
```

1. **Navigate to the `module_1` directory:**
   ```bash
   cd module_1
   ```

2. **Build and start the container:**
   ```bash
   docker compose up --build
   ```
   *(To run in the background as a daemon, add the `-d` flag: `docker compose up -d --build`)*

3. **Open the Dashboard in your browser:**
   ```
   http://localhost:8000
   ```

4. **Stop the container:**
   ```bash
   docker compose down
   ```

---
