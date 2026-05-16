#!/usr/bin/env python3
"""Continuous trading loop for hermes-trader."""
import os
import time
import logging

# Load .env.local
env_path = '.env.local'
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, _, val = line.partition('=')
                os.environ[key.strip()] = val.strip()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s:%(name)s:%(message)s'
)

from hermes_agent.agents.perception import scan_once
from hermes_agent.agents.research import research
from hermes_agent.agents.executor import maybe_execute
from hermes_agent.agents.config import get_config
from hermes_agent.client.universe import get_universe

logger = logging.getLogger(__name__)

logger.info("=== HERMES TRADER - Starting Continuous Trading Loop ===")
logger.info(f"Mode: LIVE")

config = get_config()
universe = get_universe()

scan_interval = config.get('scan_interval_sec', 180)
min_score = config.get('min_composite_score', 20)

logger.info(f"Scan interval: {scan_interval}s, Min score: {min_score}")

while True:
    try:
        logger.info(f"Scanning markets...")
        results = scan_once(universe=universe, min_score=min_score, config=config)
        logger.info(f"Scan found {len(results)} triggers")
        
        for perception in results:
            coin = perception['coin']
            score = perception.get('composite_score', 0)
            logger.info(f"Researching {coin} (score: {score:.1f})...")
            
            try:
                analysis = research(coin, perception)
                logger.info(f"Verdict: {analysis['verdict']}, Confidence: {analysis['confidence']}")
                
                if analysis['verdict'] in ('LONG', 'SHORT'):
                    logger.info(f"Executing {analysis['side']} trade...")
                    result = maybe_execute(analysis)
                    logger.info(f"Trade result: {result}")
            except Exception as e:
                logger.error(f"Error processing {coin}: {e}")
        
        logger.info(f"Sleeping {scan_interval}s until next scan...")
        time.sleep(scan_interval)
        
    except KeyboardInterrupt:
        logger.info("Trading loop stopped by user")
        break
    except Exception as e:
        logger.error(f"Trading loop error: {e}")
        logger.info(f"Sleeping 60s before retry...")
        time.sleep(60)
