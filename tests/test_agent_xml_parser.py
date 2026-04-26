"""测试 AGENT XML 解析器"""
from ui.agent.xml_parser import ParsedResponse


def test_tool_call_xml():
    xml = '''<response>
  <thinking>用户请求下载最新版，需要先获取版本列表</thinking>
  <message>正在获取可用版本列表...</message>
  <action type="tool_call">
    <tool>get_available_versions</tool>
    <params>
    </params>
  </action>
</response>'''
    p = ParsedResponse.parse(xml)
    assert p.thinking == "用户请求下载最新版，需要先获取版本列表"
    assert p.message == "正在获取可用版本列表..."
    assert p.action_type == "tool_call"
    assert p.tool_name == "get_available_versions"
    assert p.is_tool_call() == True
    assert p.is_await_choice() == False
    assert p.is_complete() == False


def test_await_choice_xml():
    xml = '''<response>
  <thinking>发现多个1.20.1版本</thinking>
  <message>检测到多个 1.20.1 版本，请选择要启动的版本：</message>
  <action type="await_choice">
    <options>
      <option value="1.20.1">原版 1.20.1</option>
      <option value="1.20.1-forge-49.0.26">Forge版 1.20.1</option>
      <option value="1.20.1-fabric-0.15.11">Fabric版 1.20.1</option>
    </options>
  </action>
</response>'''
    p = ParsedResponse.parse(xml)
    assert p.is_await_choice() == True
    assert len(p.options) == 3
    assert p.options[0]["value"] == "1.20.1"
    assert p.options[0]["label"] == "原版 1.20.1"


def test_complete_xml():
    xml = '''<response>
  <thinking>所有操作已完成</thinking>
  <message>Minecraft 1.20.1 已安装完成！</message>
  <action type="complete" />
</response>'''
    p = ParsedResponse.parse(xml)
    assert p.is_complete() == True
    assert p.message == "Minecraft 1.20.1 已安装完成！"


def test_install_with_params():
    xml = '''<response>
  <thinking>用户选择的版本是最新正式版</thinking>
  <message>正在下载并安装最新正式版...</message>
  <action type="tool_call">
    <tool>install_version</tool>
    <params>
      <param name="version_id">1.21.4</param>
      <param name="mod_loader">无</param>
    </params>
  </action>
</response>'''
    p = ParsedResponse.parse(xml)
    assert p.tool_name == "install_version"
    assert p.tool_params["version_id"] == "1.21.4"
    assert p.tool_params["mod_loader"] == "无"


def test_install_with_parameter_tag():
    xml = '''<response>
  <thinking>用户选择的版本是最新正式版</thinking>
  <message>正在下载并安装最新正式版...</message>
  <action type="tool_call">
    <tool>install_version</tool>
    <params>
      <parameter name="version_id">1.21.4</parameter>
      <parameter name="mod_loader">Forge</parameter>
    </params>
  </action>
</response>'''
    p = ParsedResponse.parse(xml)
    assert p.tool_name == "install_version"
    assert p.tool_params["version_id"] == "1.21.4"
    assert p.tool_params["mod_loader"] == "Forge"


def test_search_mods_params():
    xml = '''<response>
  <thinking>需要在Modrinth搜索钠模组</thinking>
  <message>正在 Modrinth 搜索 Sodium...</message>
  <action type="tool_call">
    <tool>search_mods</tool>
    <params>
      <param name="query">sodium</param>
      <param name="game_version">1.20.1</param>
      <param name="mod_loader">fabric</param>
    </params>
  </action>
</response>'''
    p = ParsedResponse.parse(xml)
    assert p.tool_name == "search_mods"
    assert p.tool_params["query"] == "sodium"
    assert p.tool_params["game_version"] == "1.20.1"
    assert p.tool_params["mod_loader"] == "fabric"


def test_no_action():
    xml = '''<response>
  <thinking>简单回复</thinking>
  <message>你好！有什么可以帮你的吗？</message>
</response>'''
    p = ParsedResponse.parse(xml)
    assert p.has_action() == False
    assert p.is_tool_call() == False
    assert p.is_await_choice() == False
    assert p.is_complete() == False
