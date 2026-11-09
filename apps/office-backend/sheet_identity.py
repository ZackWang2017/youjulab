from copy import deepcopy


def sheet_content_for_asset(asset_id: str, content):
    """Reject foreign workbook snapshots instead of silently changing ownership."""
    if not isinstance(content, dict):
        return content
    workbook_id = content.get("id")
    if workbook_id not in (None, "", asset_id):
        raise ValueError("工作簿归属与当前资产不一致，已阻止保存。请核对原始数据。")
    result = deepcopy(content)
    if "sheets" in result or "sheetOrder" in result:
        result["id"] = asset_id
    return result
