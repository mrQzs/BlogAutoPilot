"""测试 HTML 清洗"""

from blog_autopilot.publisher import sanitize_html


class TestSanitizeHtml:

    def test_clean_html_unchanged(self):
        html = "<p>Hello <strong>world</strong></p>"
        assert sanitize_html(html) == html

    def test_remove_script_tag(self):
        html = '<p>Safe</p><script>alert("xss")</script><p>Also safe</p>'
        result = sanitize_html(html)
        assert "<script>" not in result
        assert "alert" not in result
        assert "<p>Safe</p>" in result

    def test_remove_iframe(self):
        html = '<p>Content</p><iframe src="evil.com"></iframe>'
        result = sanitize_html(html)
        assert "<iframe" not in result

    def test_remove_event_handlers(self):
        html = '<p onclick="alert(1)">Click me</p>'
        result = sanitize_html(html)
        assert "onclick" not in result

    def test_remove_javascript_protocol(self):
        html = '<a href="javascript:alert(1)">Link</a>'
        result = sanitize_html(html)
        assert "javascript:" not in result

    def test_allow_data_image(self):
        html = '<img src="data:image/png;base64,abc123">'
        result = sanitize_html(html)
        assert "data:image" in result

    def test_remove_data_non_image(self):
        html = '<a href="data:text/html,<script>alert(1)</script>">Link</a>'
        result = sanitize_html(html)
        assert "data:text" not in result

    def test_empty_input(self):
        assert sanitize_html("") == ""
        assert sanitize_html(None) is None

    def test_self_closing_dangerous_tags(self):
        html = '<p>Safe</p><input type="text" /><p>Also safe</p>'
        result = sanitize_html(html)
        assert "<input" not in result

    def test_script_closing_tag_with_space(self):
        """闭合标签带空格不应绕过清洗"""
        html = '<p>Safe</p><script >alert(1)</script ><p>Also safe</p>'
        result = sanitize_html(html)
        assert "<script" not in result
        assert "alert" not in result
        assert "<p>Safe</p>" in result

    def test_unclosed_script_tag(self):
        """未闭合的 script 标签也应被移除"""
        html = '<p>Safe</p><script>alert(1)'
        result = sanitize_html(html)
        assert "<script" not in result
