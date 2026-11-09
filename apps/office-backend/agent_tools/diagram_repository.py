"""Diagram lookup and persistence using the authenticated Office Store client."""
from agent_tools.errors import OfficeError


def find_diagram(api, project_id: str, name: str):
    matches = [asset for asset in api.assets(project_id, 'diagram') if asset.get('name') == name]
    if len(matches) > 1:
        raise ValueError('存在同名绘图，请使用 asset_id 定位。')
    return matches[0] if matches else None


def load_diagram(api, project_id: str | None, name: str, asset_id: str = ''):
    found = {'id': asset_id} if asset_id else find_diagram(api, project_id, name)
    if not found:
        raise OfficeError(404, f'绘图「{name}」不存在。')
    asset = api.get_asset(found['id'])
    if asset.get('kind') != 'diagram':
        raise ValueError('该资产不是 draw.io 绘图。')
    if project_id and asset.get('project_id') not in {None, project_id}:
        raise OfficeError(404, '该绘图不属于指定项目。')
    if name and asset.get('name') != name:
        raise ValueError('asset_id 与 diagram_name 不一致。')
    return asset


def create_diagram_asset(api, project_id, name, content, visibility):
    return api.create_asset(project_id, name, kind='diagram', content=content, visibility=visibility)


def save_diagram_asset(api, asset, content, expected_revision):
    return api.save_asset(asset['id'], content, expected_revision)
