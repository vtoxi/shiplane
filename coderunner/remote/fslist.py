import json, os, sys

path = sys.argv[1]
out = {'path': path, 'exists': os.path.isdir(path), 'dirs': [], 'file_count': 0, 'writable': False}
if out['exists']:
    out['writable'] = os.access(path, os.W_OK)
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith('.'):
                    continue
                if e.is_dir(follow_symlinks=False):
                    out['dirs'].append(e.name)
                else:
                    out['file_count'] += 1
    except OSError as e:
        out['error'] = str(e)
    out['dirs'] = sorted(out['dirs'])[:500]
print(json.dumps(out))
