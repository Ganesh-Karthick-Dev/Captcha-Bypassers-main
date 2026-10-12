"""Unit tests for common/telemetry.py generic pipeline tracer."""
import logging
import time
import unittest

from common.telemetry import PipelineTracer, TerminalStatus


class TestPipelineTracer(unittest.TestCase):
    def setUp(self):
        logging.basicConfig(level=logging.INFO)

    def test_pipeline_tracer_timing(self):
        tracer = PipelineTracer(workflow_name="test_pipeline")
        
        with tracer.trace_phase("fetch", component_state="connecting"):
            time.sleep(0.05)

        with tracer.trace_phase("classify", component_state="evaluating"):
            time.sleep(0.05)

        summary = tracer.summary()
        self.assertEqual(summary["workflow"], "test_pipeline")
        self.assertEqual(summary["phases_count"], 2)
        self.assertGreaterEqual(summary["total_elapsed"], 0.10)
        self.assertEqual(summary["phases"][0]["phase"], "fetch")
        self.assertEqual(summary["phases"][0]["status"], TerminalStatus.SUCCESS.value)
        self.assertEqual(summary["phases"][1]["phase"], "classify")

    def test_pipeline_tracer_error_handling(self):
        tracer = PipelineTracer(workflow_name="error_pipeline")
        
        with self.assertRaises(ValueError):
            with tracer.trace_phase("validation", component_state="verifying"):
                raise ValueError("invalid payload format")

        summary = tracer.summary()
        self.assertEqual(summary["phases_count"], 1)
        self.assertEqual(summary["phases"][0]["status"], TerminalStatus.EXECUTION_ERROR.value)
        self.assertEqual(summary["phases"][0]["error"], "invalid payload format")


if __name__ == "__main__":
    unittest.main()
