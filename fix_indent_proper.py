with open('cbor_cddl_analyzer.py', 'r') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    if 96 <= i <= 237:
        if line.strip(): # if not empty
            if line.startswith('    '):
                new_lines.append('    ' + line)
            else:
                new_lines.append('        ' + line)
        else:
            new_lines.append(line)
    else:
        new_lines.append(line)

with open('cbor_cddl_analyzer.py', 'w') as f:
    f.writelines(new_lines)
