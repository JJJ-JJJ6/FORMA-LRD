import asyncio
import os
import logging
import csv
import numpy as np
from dotenv import load_dotenv

import sys
import time
import aiohttp
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from AstroAgent.core.config.all_config import AllConfig
from AstroAgent.workflow_orchestrator import WorkflowOrchestrator
from AstroAgent.manager.runtime.state_manager import SpectroStateFactory
from AstroAgent.agents.common.result_writer import ResultWriter

MCP_SERVER_SCRIPT_FALLBACK = str(PROJECT_ROOT / 'src' / 'AstroAgent' / 'mcp_tools' / 'spectro_server.py')


def _get_mcp_server_url(configs) -> str:
    """Extract the MCP server URL from the loaded config (mcp_config.json)."""
    for server_cfg in configs.mcp.config.values():
        url = server_cfg.get("url")
        if url:
            return url
    raise ValueError("No 'url' found in mcp_config.json — is transport set to streamable-http?")


async def start_mcp_server(mcp_url: str, server_script: str, startup_timeout: int) -> asyncio.subprocess.Process:
    """Launch MCP server as a background subprocess and wait until it is ready."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable, server_script,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    logging.info(f"MCP server process started (pid={proc.pid}), waiting for readiness...")

    deadline = time.monotonic() + startup_timeout
    async with aiohttp.ClientSession() as session:
        while time.monotonic() < deadline:
            # Check if process has already exited
            if proc.returncode is not None:
                stdout, stderr = await proc.communicate()
                raise RuntimeError(
                    f"MCP server exited early (code={proc.returncode}).\n"
                    f"stderr: {stderr.decode(errors='replace')}\n"
                    f"stdout: {stdout.decode(errors='replace')}"
                )
            try:
                async with session.get(
                    mcp_url,
                    headers={"Accept": "text/event-stream"},
                    timeout=aiohttp.ClientTimeout(total=1),
                ) as resp:
                    # 200 or 405/406 all mean server is up and responding
                    if resp.status < 500:
                        logging.info("✅ MCP server is ready")
                        return proc
            except Exception:
                await asyncio.sleep(0.5)

    # Timeout: terminate gracefully, guard against already-dead process
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
    raise RuntimeError(f"MCP server did not become ready within {startup_timeout}s")


async def main():
    """Main asynchronous entry: unified single / batch image analysis"""
    mcp_proc = None
    try:
        load_dotenv()

        # ------------------------
        # Load configs
        # ------------------------
        configs = AllConfig.from_env()
        # model_config = configs.model
        io_config = configs.io
        batch_config = configs.batch
        # params_config = configs.params
        factory = SpectroStateFactory(configs)
        writer = ResultWriter()

        input_dir = io_config.input_dir
        output_dir = io_config.output_dir

        # ------------------------
        # Start MCP server (URL read from mcp_config.json via configs)
        # ------------------------
        mcp_url = _get_mcp_server_url(configs)
        server_script = configs.mcp.server_script or MCP_SERVER_SCRIPT_FALLBACK
        mcp_proc = await start_mcp_server(
            mcp_url=mcp_url,
            server_script=server_script,
            startup_timeout=configs.mcp.startup_timeout,
        )

        # ------------------------
        # Check directories
        # ------------------------
        if not input_dir:
            logging.error("INPUT_DIR is not set")
            return

        if not os.path.isdir(input_dir):
            logging.error(f"Input directory does not exist: {input_dir}")
            return

        if not output_dir:
            logging.error("OUTPUT_DIR is not set")
            return

        if not os.path.isdir(output_dir):
            logging.info(f"Output directory does not exist, creating: {output_dir}")
            os.makedirs(output_dir, exist_ok=True)

        logging.info(f"Using MCP config: {configs.mcp.path}")

        # ------------------------
        # Resolve image IDs (single or batch)
        # ------------------------
        if io_config.run_mode=='b':
            if batch_config.is_batch_mode():
                file_ids = batch_config.generate_ids()
                logging.info(f"Batch mode enabled, total images: {len(file_ids)}")
            else: 
                raise ValueError("Batch mode is enabled but start/end are not set")
        else:
            if not io_config.file_name:
                logging.error("IMAGE_NAME is not set for single image mode")
                return
            file_ids = [io_config.file_name]
            logging.info("Single image mode enabled")

        # ------------------------
        # Initialize orchestrator
        # ------------------------
        orchestrator = WorkflowOrchestrator(configs)
        # await orchestrator.initialize()

        # ------------------------
        # Helper for safe string conversion
        # ------------------------
        def safe_str(x):
            if x is None or (isinstance(x, float) and (np.isnan(x) or np.isinf(x))):
                return "N/A"
            return str(x)

        # ------------------------
        # Prepare incremental CSV output
        # ------------------------
        csv_path = os.path.join(output_dir, "in_brief.csv")
        csv_header = [
            "file_name",
            "type",
            "signal_clarity",
            "redshift",
            "redshift_rms",
            "lines",
            "confidence",
            "human_review",
        ]
        # Write header once at the start (overwrite any previous run)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(csv_header)

        # ------------------------
        # Main processing loop
        # ------------------------
        success_count = 0
        batch_num = 0  # batch failure analysis counter
        total = len(file_ids)

        for idx, file_name in enumerate(file_ids, start=1):
            format = io_config.input_format
            file_path = os.path.join(input_dir, f"{file_name}.{format}")

            if not os.path.isfile(file_path):
                logging.warning(f"Skipping missing file: {file_path}")
                continue

            logging.info(f"Processing image {idx}/{total}: {file_name}.{format}")

            try:
                state = factory.create(
                    file_name=file_name,
                    input_dir=input_dir,
                    output_dir=output_dir
                )
                result = await orchestrator.run_analysis_single(state)
                writer.write(result)
                logging.info(f"Image {file_name}.{format} processed")

                in_brief = result.get("in_brief", {})
                lines_val = in_brief.get("lines")
                if isinstance(lines_val, list):
                    lines_str = ", ".join(str(l) for l in lines_val)
                else:
                    lines_str = safe_str(lines_val)

                row = [
                    file_name,
                    safe_str(in_brief.get("type")),
                    safe_str(in_brief.get("signal_clarity")),
                    safe_str(in_brief.get("redshift")),
                    safe_str(in_brief.get("redshift_rms")),
                    lines_str,
                    safe_str(in_brief.get("confidence")),
                    safe_str(in_brief.get("human_review")),
                ]

                # Append result immediately so it survives later failures
                with open(csv_path, "a", newline="", encoding="utf-8") as f:
                    csv.writer(f).writerow(row)
                success_count += 1

                # ── Batch failure analysis trigger ──────────────────
                if configs.params.self_evolve and result.get("_failure_recorded"):
                    from AstroAgent.agents.multi_agents.utils.RA import (
                        _read_pending_failures,
                        analyze_failure_batch,
                    )
                    pending = _read_pending_failures(output_dir)
                    pending_count = len(pending)
                    batch_size = configs.params.failure_batch_size
                    logging.info(
                        f"Self-evolve: failure recorded, pending batch: "
                        f"{pending_count}/{batch_size}"
                    )
                    if pending_count >= batch_size:
                        batch_num += 1
                        logging.info(
                            f"Triggering batch failure analysis #{batch_num} "
                            f"({pending_count} failures)"
                        )
                        try:
                            await analyze_failure_batch(
                                output_dir=output_dir,
                                batch_num=batch_num,
                                model=configs.model.llm["model"],
                                api_key=configs.model.llm["api_key"],
                                base_url=configs.model.llm["base_url"],
                            )
                        except Exception as exc:
                            logging.exception(
                                f"Batch failure analysis #{batch_num} failed: {exc}"
                            )

            except Exception as e:
                logging.exception(f"Failed to process image {file_name}.{format}: {e}")

        if success_count:
            logging.info(f"{success_count} results saved to {csv_path}")
        else:
            logging.warning("No valid results to save")

        # ── Final batch flush: process any remaining pending failures ──
        if configs.params.self_evolve:
            from AstroAgent.agents.multi_agents.utils.RA import (
                _read_pending_failures,
                analyze_failure_batch,
            )
            pending = _read_pending_failures(output_dir)
            if pending:
                batch_num += 1
                logging.info(
                    f"Final flush: triggering batch failure analysis #{batch_num} "
                    f"({len(pending)} remaining failures)"
                )
                try:
                    await analyze_failure_batch(
                        output_dir=output_dir,
                        batch_num=batch_num,
                        model=configs.model.llm["model"],
                        api_key=configs.model.llm["api_key"],
                        base_url=configs.model.llm["base_url"],
                    )
                except Exception as exc:
                    logging.exception(
                        f"Final batch failure analysis #{batch_num} failed: {exc}"
                    )

        logging.info("All tasks completed")

    except Exception as e:
        logging.exception(f"Main program failed: {e}")

    finally:
        if mcp_proc is not None and mcp_proc.returncode is None:
            mcp_proc.terminate()
            try:
                await asyncio.wait_for(mcp_proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                mcp_proc.kill()
            logging.info("MCP server process terminated")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    asyncio.run(main())
