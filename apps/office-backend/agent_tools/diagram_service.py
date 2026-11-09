"""External-agent draw.io tools. Persist assets without controlling an iframe."""
from agent_tools.diagram_repository import create_diagram_asset, find_diagram, load_diagram, save_diagram_asset
from agent_tools.errors import OfficeError
from agent_tools.spreadsheet_service import _ensure_project, _ready
from agent_tools.workbook_service import trim_name
from drawio import describe_diagram, diagram_content, diagram_input


def _result(asset, action, include_xml=False):
    content = diagram_content(asset.get('content'))
    pages = describe_diagram(content)
    result = {'ok': True, 'action': action, 'asset_id': asset['id'], 'diagram_name': asset['name'],
              'project_id': asset.get('project_id'), 'visibility': asset.get('placement', 'project'),
              'revision': asset.get('revision', 0), 'format': 'drawio-v1',
              'page_count': len(pages), 'node_count': sum(len(p['nodes']) for p in pages),
              'edge_count': sum(len(p['edges']) for p in pages)}
    if action == 'read':
        result['pages'] = pages
    if include_xml:
        result['xml'] = content['xml']
    return result


def create_diagram(project_name: str, diagram_name: str, graph: dict | None = None,
                   xml: str = '', visibility: str = 'project') -> dict:
    """Create a draw.io asset from graph OR XML; duplicate names fail. No browser required.

    graph: {nodes:[{id,text,shape,x?,y?,width?,height?,fill?,stroke?}],
    edges:[{id?,source,target,text?}],layout?:{direction:'left-to-right'|'top-to-bottom'}}.
    Omitting both graph and XML creates a blank canvas. visibility: project or user.
    """
    api = _ready()
    project_name = trim_name(project_name, '项目名')
    diagram_name = trim_name(diagram_name, '绘图名')
    if visibility not in {'project', 'user'}:
        raise ValueError('visibility 应为 project 或 user。')
    content = diagram_input(graph, xml)
    project, created = _ensure_project(api, project_name, create=True)
    if find_diagram(api, project['id'], diagram_name):
        raise OfficeError(409, f'绘图「{diagram_name}」已存在，请读取后更新，或使用新名称。')
    asset = create_diagram_asset(api, project['id'], diagram_name, content, visibility)
    return {**_result(asset, 'create'), 'project_name': project_name, 'created_project': created}


def _resolve(api, project_name, diagram_name, asset_id):
    project = None
    if project_name:
        project, _ = _ensure_project(api, trim_name(project_name, '项目名'), create=False)
    if not asset_id and (not project or not diagram_name):
        raise ValueError('请提供 asset_id，或同时提供 project_name 和 diagram_name。')
    name = trim_name(diagram_name, '绘图名') if diagram_name else ''
    return load_diagram(api, project['id'] if project else None, name, asset_id)


def read_diagram(project_name: str = '', diagram_name: str = '', asset_id: str = '', include_xml: bool = False) -> dict:
    """Read a diagram's revision and pages/nodes/edges. Use asset_id or exact project/diagram names.

    Set include_xml=true before editing an existing diagram to preserve layout, groups and styles.
    Never creates missing assets. The node summary is NOT a lossless round-trip format.
    """
    api = _ready()
    return _result(_resolve(api, project_name, diagram_name, asset_id), 'read', include_xml)


def write_diagram(expected_revision: int, project_name: str = '', diagram_name: str = '',
                  asset_id: str = '', graph: dict | None = None, xml: str = '') -> dict:
    """Replace the WHOLE drawing using graph OR XML and the revision returned by read_diagram.

    Never creates missing assets or retries a conflict. On 409 read again and reconcile user edits.
    graph rebuilds all pages; use XML for preserving existing complex diagrams.
    """
    api = _ready()
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('expected_revision 必须是读取到的非负整数版本号。')
    if graph is None and not xml:
        raise ValueError('更新必须提供 graph 或 xml；空画布请显式提供 graph={"nodes":[],"edges":[]}。')
    asset = _resolve(api, project_name, diagram_name, asset_id)
    if expected_revision != asset.get('revision', 0):
        raise OfficeError(409, '绘图已更新，请重新读取后合并修改，不要直接覆盖。')
    content = diagram_input(graph, xml)
    saved = save_diagram_asset(api, asset, content, expected_revision)
    return _result(saved, 'write')
