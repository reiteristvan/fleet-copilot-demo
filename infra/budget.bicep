targetScope = 'subscription'

@description('Budget name.')
param name string = 'budget-fleet-copilot'

@description('Monthly cap, expressed in the subscription billing currency.')
@minValue(1)
param amount int = 40

@description('Addresses notified when a threshold is crossed.')
@minLength(1)
param contactEmails array

// Budgets must start on the first of a month; utcNow() is only legal in a
// parameter default, which is why this is a parameter and not a variable.
param startDate string = '${utcNow('yyyy-MM')}-01'

resource budget 'Microsoft.Consumption/budgets@2024-08-01' = {
  name: name
  properties: {
    category: 'Cost'
    amount: amount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: startDate
    }
    notifications: {
      actualHalf: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 50
        thresholdType: 'Actual'
        contactEmails: contactEmails
      }
      actualEighty: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: contactEmails
      }
      forecastedFull: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 100
        thresholdType: 'Forecasted'
        contactEmails: contactEmails
      }
    }
  }
}

output budgetId string = budget.id
