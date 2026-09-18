targetScope = 'subscription'

@description('Budget name.')
param name string = 'budget-fleet-copilot'

@description('Monthly cap, expressed in the subscription billing currency.')
@minValue(1)
param amount int = 40

@description('Addresses notified when a threshold is crossed.')
@minLength(1)
param contactEmails array

@description('Environment slug, used to derive the resource group the filter points at.')
@allowed([
  'dev'
  'ci'
])
param environmentName string

@description('Document Intelligence account name, as deployed by main.bicep.')
param documentIntelligenceAccountName string

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

// Budget filters match on a lowercased resource id. Building it by hand rather
// than taking an output keeps this template deployable on its own, which is the
// whole reason it is not part of main.bicep.
var documentIntelligenceResourceId = toLower(
  '/subscriptions/${subscription().subscriptionId}/resourceGroups/rg-fleet-copilot-${environmentName}/providers/Microsoft.CognitiveServices/accounts/${documentIntelligenceAccountName}'
)

resource documentIntelligenceBudget 'Microsoft.Consumption/budgets@2024-08-01' = {
  name: '${name}-docintel'
  properties: {
    category: 'Cost'
    // Deliberately far below the subscription cap. Layout analysis bills per
    // page, and a runaway loop over the corpus is cheap enough to go unnoticed
    // against a $40 ceiling; this is the alert that would actually fire.
    amount: 15
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: startDate
    }
    filter: {
      dimensions: {
        name: 'ResourceId'
        operator: 'In'
        values: [
          documentIntelligenceResourceId
        ]
      }
    }
    notifications: {
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
output documentIntelligenceBudgetId string = documentIntelligenceBudget.id
