"""Resolve settings text and declarative forms on the backend."""
from copy import deepcopy
from module.server.i18n import Addition


def present_settings(task, payload):
    translations = Addition.load_additions()

    def text(value, fallback=None):
        return {locale.replace('-', '_'): table.get(value, fallback if fallback is not None else value)
                for locale, table in translations.items()}

    def localize(node):
        if isinstance(node, list):
            return [localize(item) for item in node]
        if not isinstance(node, dict):
            return node
        result = {}
        for key, value in node.items():
            if key in ('title', 'label') and isinstance(value, str):
                result[key] = text(value)
            elif key == 'labels':
                result[key] = {field: text(label) for field, label in value.items()}
            else:
                result[key] = localize(value)
        return result

    forms = getattr(task, 'settings_forms', {})
    options = getattr(task, 'settings_options', {})
    for group, rows in payload.items():
        if not rows:
            continue
        rows[0]['ui_group_title'] = text(group)
        if group in forms:
            rows[0]['ui_form'] = localize(deepcopy(forms[group]))
        for row in rows:
            choices = options.get(group, {}).get(row['name'])
            if choices:
                row['enumEnum'] = choices
                if row['default'] not in choices:
                    row['default'] = choices[-1]
            title = row.get('title', row['name'])
            # Explicit prose takes precedence; schema-generated English titles use the field dictionary.
            explicit = any(ord(char) > 127 for char in title)
            row['ui_text'] = {
                locale.replace('-', '_'): {
                    'title': title if explicit and locale != 'en-US' else table.get(row['name'], table.get(title, title)),
                    'description': table.get(row.get('description', ''), row.get('description', '')),
                    'options': {option: table.get(option, option) for option in row.get('enumEnum', [])},
                } for locale, table in translations.items()
            }
            if 'ui_layout' in row:
                row['ui_layout'] = localize(deepcopy(row['ui_layout']))
    return payload
