import json
import sys
import os
import urllib.request
import urllib.error

BACKEND_URL = os.environ.get('AOQI_BACKEND_URL', 'http://127.0.0.1:5000')
TOOLS_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), 'tools.schema.json')

def load_tools_schema():
    with open(TOOLS_SCHEMA_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def call_backend(path, method='GET', data=None):
    url = f'{BACKEND_URL}{path}'
    if data is not None:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method=method
        )
    else:
        req = urllib.request.Request(url, method=method)
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return {'error': f'HTTP {e.code}: {e.read().decode("utf-8")}'}
    except Exception as e:
        return {'error': str(e)}

def handle_tools_list():
    schema = load_tools_schema()
    return {
        'jsonrpc': '2.0',
        'id': None,
        'result': {
            'tools': schema['tools']
        }
    }

def handle_tools_call(params):
    tool_name = params.get('name', '')
    arguments = params.get('arguments', {})
    
    path_map = {
        'aoqi_search_challenges': ('/api/challenges', 'GET', lambda a: {}),
        'aoqi_get_challenge': ('/api/challenge/', 'GET', lambda a: a.get('name', '')),
        'aoqi_search_knowledge': ('/api/knowledge/search', 'POST', lambda a: a),
        'aoqi_get_knowledge_page': ('/api/knowledge/page/', 'GET', lambda a: a.get('slug', '')),
        'aoqi_compare_pets': ('/api/compare', 'POST', lambda a: a),
        'aoqi_recommend_team': ('/api/recommend', 'POST', lambda a: a),
        'aoqi_analyze_challenge': ('/api/challenge/analyze', 'POST', lambda a: a),
        'aoqi_validate_team': ('/api/team/validate', 'POST', lambda a: a),
        'aoqi_get_knowledge_stats': ('/api/knowledge/stats', 'GET', lambda a: {}),
    }
    
    if tool_name not in path_map:
        return {
            'jsonrpc': '2.0',
            'id': params.get('id'),
            'error': {'code': -32601, 'message': f'Unknown tool: {tool_name}'}
        }
    
    base_path, method, args_fn = path_map[tool_name]
    path = base_path + args_fn(arguments) if method == 'GET' and args_fn(arguments) else base_path
    data = arguments if method == 'POST' else None
    
    result = call_backend(path, method, data)
    
    return {
        'jsonrpc': '2.0',
        'id': params.get('id'),
        'result': {
            'content': [
                {
                    'type': 'text',
                    'text': json.dumps(result, ensure_ascii=False, indent=2)
                }
            ]
        }
    }

def handle_initialize(params):
    return {
        'jsonrpc': '2.0',
        'id': params.get('id'),
        'result': {
            'protocolVersion': '2024-11-05',
            'capabilities': {
                'tools': {}
            },
            'serverInfo': {
                'name': 'aoqi-mcp-server',
                'version': '1.0.0'
            }
        }
    }

def main():
    sys.stderr.write('AoQi MCP Server started\n')
    sys.stderr.flush()
    
    buffer = ''
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            
            buffer += line
            line = line.strip()
            
            if not line:
                continue
            
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                sys.stderr.write(f'Invalid JSON: {line}\n')
                sys.stderr.flush()
                continue
            
            method = request.get('method', '')
            params_data = request.get('params', {})
            req_id = request.get('id')
            
            if method == 'initialize':
                response = handle_initialize(params_data)
            elif method == 'tools/list':
                response = handle_tools_list()
                response['id'] = req_id
            elif method == 'tools/call':
                response = handle_tools_call(params_data)
            elif method == 'notifications/initialized':
                continue
            else:
                response = {
                    'jsonrpc': '2.0',
                    'id': req_id,
                    'error': {'code': -32601, 'message': f'Method not found: {method}'}
                }
            
            response_str = json.dumps(response, ensure_ascii=False)
            sys.stdout.write(response_str + '\n')
            sys.stdout.flush()
            
        except Exception as e:
            sys.stderr.write(f'Error: {e}\n')
            sys.stderr.flush()

if __name__ == '__main__':
    main()
