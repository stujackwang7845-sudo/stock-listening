from FinMind.data import DataLoader

dl = DataLoader()
print("Methods in DataLoader:")
for m in dir(dl):
    if not m.startswith('_'):
        print(m)
