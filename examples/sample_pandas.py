import pandas as pd

df = pd.read_csv("sales.csv")

df["total"] = df["price"] * df["quantity"]
df_filtered = df[df["total"] > 100]

summary = df_filtered.groupby("region").agg({"total": "sum", "quantity": "sum"})
summary = summary.sort_values("total", ascending=False)

merged = pd.merge(summary, region_meta, on="region", how="left")

print(merged.head(10))
merged.to_csv("summary_output.csv", index=False)
