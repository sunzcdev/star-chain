#!/bin/bash
# Wrapper script — OPENAI_API_KEY is inherited from parent shell
cd /home/ubuntu/projects/agent-channel
source .venv/bin/activate
python "$@"
