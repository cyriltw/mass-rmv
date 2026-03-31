# Weights & Biases (wandb) Setup for RMV Appointment Monitor

## What This Gives You

With wandb integration, you'll get:
- **Real-time tracking** of all appointment changes
- **Beautiful visualizations** of patterns over time
- **Historical analysis** of when appointments are released
- **Optimization insights** for check frequency and timing

## Setup Steps

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Create wandb Account
1. Go to [wandb.ai](https://wandb.ai)
2. Sign up for a free account
3. Get your API key from your profile settings

### 3. Login to wandb
```bash
wandb login
# Enter your API key when prompted
```

### 4. Run the Monitor
```bash
python monitor.py
```

## What Gets Tracked

### Event Types
- **`expired_replaced`**: Old appointment expired, new one detected
- **`new_availability`**: Location became available again
- **`earlier_appointment`**: Found an earlier appointment
- **`first_appointment`**: First time seeing this location
- **`initial_population`**: Populating empty state
- **`no_change`**: No changes detected

### Metrics Tracked
- **Location ID & Name**
- **Previous vs. New appointment times**
- **Time differences** between changes
- **Day of week, hour, month** patterns
- **Check frequency** being used
- **Timestamps** of all events

## Dashboard Features

### 1. Appointment Change Patterns
- See when appointments are most likely to change
- Identify optimal check times
- Track response time to new appointments

### 2. Location Analysis
- Compare different RMV locations
- See which locations are most dynamic
- Track availability patterns

### 3. System Performance
- Monitor false positive rates
- Optimize check frequency
- Track system efficiency

## Example Queries

### Find Optimal Check Times
```python
# In wandb dashboard, create a line plot:
# X: hour_of_day, Y: count of appointment changes
# This shows when appointments are most likely to be released
```

### Analyze Location Patterns
```python
# Create a bar chart:
# X: location_name, Y: count of changes
# This shows which locations are most active
```

### Optimize Check Frequency
```python
# Create a scatter plot:
# X: check_frequency_minutes, Y: time_to_detect_new_appointment
# This helps find the sweet spot for checking
```

## Benefits

1. **Data-Driven Decisions**: See actual patterns instead of guessing
2. **Optimization**: Find the best times to check and optimal frequency
3. **Historical Analysis**: Track changes over weeks/months
4. **Alerting**: Set up alerts for unusual patterns
5. **Collaboration**: Share insights with others monitoring RMV

## Troubleshooting

### wandb Not Initializing
- Check your internet connection
- Verify your API key is correct
- Try `wandb login` again

### No Data Showing
- Ensure the monitor is running
- Check that appointments are actually changing
- Verify wandb.run.step is incrementing

### Performance Issues
- wandb adds minimal overhead
- Data is sent asynchronously
- Can disable by setting `wandb_run = None`

## Next Steps

1. **Run the monitor** for a few days to collect data
2. **Explore the wandb dashboard** to see initial patterns
3. **Create custom visualizations** for your specific needs
4. **Set up alerts** for important patterns
5. **Optimize your monitoring strategy** based on data insights

Happy monitoring! 🚗📅
