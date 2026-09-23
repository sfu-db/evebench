import os
import csv
import sys

def count_valid_rows_in_csv(folder_path):
    results = {}
    
    # Increase the CSV field size limit
    maxInt = sys.maxsize
    while True:
        try:
            csv.field_size_limit(maxInt)
            break
        except OverflowError:
            maxInt = int(maxInt/10)

    # Iterate through all files in the folder
    for file_name in os.listdir(folder_path):
        if file_name.endswith('.csv'):
            file_path = os.path.join(folder_path, file_name)
            
            # Count rows where counterexample_valid is 'Y'
            count = 0
            with open(file_path, 'r') as csv_file:
                reader = csv.DictReader(csv_file)
                for row in reader:
                    if row.get('counterexample_valid') == 'Y':
                        count += 1

            results[file_name] = count

    return results

def parse_filename(filename):
    """Parse filename to extract dataset, prompt, model, and dummy status"""
    # Remove .csv extension
    base_name = filename.replace('.csv', '')
    
    # Check if it's a dummy file
    is_dummy = base_name.startswith('literature-dummy_')
    
    if is_dummy:
        # Remove 'literature-dummy_' prefix
        remaining = base_name.replace('literature-dummy_', '', 1)
    else:
        # Remove 'literature_' prefix
        remaining = base_name.replace('literature_', '', 1)
    
    # Split by last underscore to separate model from the rest
    parts = remaining.rsplit('_', 1)
    if len(parts) != 2:
        return None, None, None
    
    prompt, model = parts
    return prompt, model, 'Y' if is_dummy else 'N'

def generate_results_csv(results, output_path):
    """Generate a CSV file with organized results"""
    # Define the models and prompts
    models = ['o3-mini', 'gpt-4o', 'gpt-5', 'deepseek-chat', 'deepseek-reasoner', 
              'gemini-2.5-flash', 'XiYanSQL-7B', 'QWen2.5-7B']
    prompts = ['baseline', 'baseline2', 'condition-CoT2', 'error-type-CoT2']
    
    # Create data structure: {(prompt, dummy): {model: count}}
    data = {}
    
    for filename, count in results.items():
        prompt, model, dummy = parse_filename(filename)
        if prompt and model:
            key = (prompt, dummy)
            if key not in data:
                data[key] = {}
            data[key][model] = count
    
    # Write CSV file
    with open(output_path, 'w', newline='') as csvfile:
        # Create header
        fieldnames = ['prompt', 'dummy'] + models
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        # Write data for each prompt and dummy combination
        
        for dummy in ['N', 'Y']:
            for prompt in prompts:
                row = {'prompt': prompt, 'dummy': dummy}
                key = (prompt, dummy)
                if key in data:
                    for model in models:
                        row[model] = data[key].get(model, '')
                else:
                    for model in models:
                        row[model] = ''
                writer.writerow(row)

if __name__ == "__main__":
    folder_path = os.path.dirname(os.path.abspath(__file__))  # Current folder
    results = count_valid_rows_in_csv(folder_path)

    # Print results as before
    for file_name, count in results.items():
        print(f"{file_name}: {count} rows where counterexample_valid is 'Y'")
    
    # Generate CSV file
    csv_output_path = os.path.join(folder_path, 'results.csv')
    generate_results_csv(results, csv_output_path)
    print(f"\nResults saved to: {csv_output_path}")