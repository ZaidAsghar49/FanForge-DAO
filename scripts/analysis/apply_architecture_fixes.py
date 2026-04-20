import re
import os

path = r'scripts/analysis/validate_model.py'
with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

new_lines = []
in_apply_filters = False
for i, line in enumerate(lines):
    if line.startswith('def apply_filters('):
        in_apply_filters = True
    elif in_apply_filters and line.startswith('def '):
        in_apply_filters = False
        
    if in_apply_filters and 'df = df[' in line and 'apply_filters(' not in line and '_assert_filter_effective' not in line:
        indent = len(line) - len(line.lstrip())
        spaces = ' ' * indent
        
        col_match = re.search(r'df\[\"([^\"]+)\"\]', line)
        col = col_match.group(1) if col_match else 'filter'
        
        new_lines.append(spaces + 'df_before = df\n')
        new_lines.append(line)
        new_lines.append(spaces + f'_assert_filter_effective(df_before, df, "{col}")\n')
    else:
        new_lines.append(line)

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)
print('Done injecting filter warnings')
