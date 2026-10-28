# Uploads one file to an S3-compatible bucket (AWS Signature V4, unsigned payload over HTTPS). Runs on the server; Python standard library only.
import datetime, hashlib, hmac, http.client, json, os, urllib.parse, sys
C = json.loads(__CONF__)
FILE = __FILE__
u = urllib.parse.urlsplit(C['endpoint'])
path = '/' + C['bucket'] + '/' + urllib.parse.quote(C['key'], safe='/-_.~')
now = datetime.datetime.utcnow()
amz, day = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')
hdr = {'host': u.netloc, 'x-amz-content-sha256': 'UNSIGNED-PAYLOAD', 'x-amz-date': amz}
signed = ';'.join(sorted(hdr))
canon = 'PUT\n%s\n\n%s\n%s\nUNSIGNED-PAYLOAD' % (path, ''.join('%s:%s\n' % (k, hdr[k]) for k in sorted(hdr)), signed)
scope = '%s/%s/s3/aws4_request' % (day, C['region'])
sts = 'AWS4-HMAC-SHA256\n%s\n%s\n%s' % (amz, scope, hashlib.sha256(canon.encode()).hexdigest())


def mac(k, m):
    return hmac.new(k, m.encode(), hashlib.sha256).digest()


k = mac(mac(mac(mac(('AWS4' + C['secret_key']).encode(), day), C['region']), 's3'), 'aws4_request')
auth = 'AWS4-HMAC-SHA256 Credential=%s/%s, SignedHeaders=%s, Signature=%s' % (C['access_key'], scope, signed, hmac.new(k, sts.encode(), hashlib.sha256).hexdigest())
conn = http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=600)
with open(FILE, 'rb') as f:
    conn.request('PUT', path, body=f, headers={'Host': u.netloc, 'x-amz-content-sha256': 'UNSIGNED-PAYLOAD', 'x-amz-date': amz, 'Authorization': auth, 'Content-Length': str(os.path.getsize(FILE))})
    r = conn.getresponse()
    body = r.read()[:300].decode('utf-8', 'replace')
if r.status // 100 != 2:
    sys.exit('the bucket refused the upload (HTTP %d): %s' % (r.status, body))
print('uploaded')
