import os
import pandas as pd

# get all csv files in current directory
csv_files = [f for f in os.listdir('.') if f.lower().endswith('.csv')]

# split files into two groups
original_files = [f for f in csv_files if "dummy" not in f.lower()]
dummy_files = [f for f in csv_files if "dummy" in f.lower()]

def compute_union(file_list):
    # list to hold boolean masks from each valid file
    masks = []
    num_rows = None

    for file in file_list:
        df = pd.read_csv(file)

        # skip if column does not exist
        if "counterexample_valid" not in df.columns:
            print(f"skipping {file} (no 'counterexample_valid' column)")
            continue

        # check row consistency
        if num_rows is None:
            num_rows = len(df)
        else:
            if len(df) != num_rows:
                raise ValueError(f"file {file} has a different number of rows")

        # create mask for this file
        mask = (df["counterexample_valid"] == "Y")
        masks.append(mask)

    # handle case where no valid files exist
    if not masks:
        return None, 0

    # compute union across all files (row-wise OR)
    union_mask = masks[0]
    for m in masks[1:]:
        union_mask = union_mask | m

    union_sum = union_mask.sum()
    return union_mask, union_sum

# compute unions for both groups
union_original_mask, union_original_sum = compute_union(original_files)
union_dummy_mask, union_dummy_sum = compute_union(dummy_files)

print("number of rows with counterexample_valid == 'Y' in original union:", union_original_sum)
print("number of rows with counterexample_valid == 'Y' in dummy union:", union_dummy_sum)
