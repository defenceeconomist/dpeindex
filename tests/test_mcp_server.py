import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'Install requirements-mcp.txt for MCP protocol tests')
class MCPProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_discovery_and_status_from_another_working_directory(self):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters
        launcher = Path(__file__).resolve().parents[1] / 'scripts/run-mcp.py'
        async with Client(StdioServerParameters(command=sys.executable, args=[str(launcher)], cwd='/tmp')) as client:
            listing = await client.list_tools()
            self.assertEqual({t.name for t in listing.tools},
                             {'dpe_fetch_redif', 'dpe_update_model', 'dpe_update_status', 'dpe_resume_update'})
            status_tool = next(t for t in listing.tools if t.name == 'dpe_update_status')
            self.assertTrue(status_tool.annotations.read_only_hint)
            result = await client.call_tool('dpe_update_status', {})
            self.assertFalse(result.is_error)
            self.assertIn(result.structured_content['status'], {'ready', 'not_trained'})
            result = await client.call_tool('dpe_resume_update', {'workspace_id': '../escape'})
            self.assertTrue(result.is_error)

    async def test_fetch_update_and_resume_round_trip_and_validation(self):
        from mcp import Client
        from app.mcp_server import server
        with patch('app.mcp_jobs.fetch_snapshot', return_value={'snapshot_id': 'example'}) as fetch, \
             patch('app.mcp_jobs.update_model', return_value={'workspace_id': 'job'}) as update, \
             patch('app.mcp_jobs.resume_update', return_value={'status': 'started'}) as resume:
            async with Client(server) as client:
                result = await client.call_tool('dpe_fetch_redif', {})
                self.assertEqual(result.structured_content['snapshot_id'], 'example')
                result = await client.call_tool('dpe_update_model', {'snapshot_id': 'example', 'device': 'cpu'})
                self.assertEqual(result.structured_content['workspace_id'], 'job')
                result = await client.call_tool('dpe_resume_update', {'workspace_id': 'job'})
                self.assertEqual(result.structured_content['status'], 'started')
                result = await client.call_tool('dpe_update_model', {'snapshot_id': 'example', 'device': 'invalid'})
                self.assertTrue(result.is_error)
            fetch.assert_called_once()
            update.assert_called_once_with('example', through=None, device='cpu', without_citations=False, allow_removals=False)
            resume.assert_called_once_with('job')


if __name__ == '__main__':
    unittest.main()
