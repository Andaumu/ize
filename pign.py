python -c "
with open('k2.py', 'r') as f:
    c = f.read()
c = c.replace('SEPA_BIN', 'SEPAY_BIN').replace('SEPA_ACCOUNT_NUMBER', 'SEPAY_ACCOUNT_NUMBER')
with open('k2.py', 'w') as f:
    f.write(c)
print('Đã thay thế xong.')
"
