# ResultEnvelope

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**Source** | **string** | object URI — feed to cat/head/export | 
**Lines** | Pointer to **[]int32** | [start,end] for text/code; null for structured | [optional] 
**Content** | Pointer to **string** | snippet to read | [optional] [default to ""]
**Score** | Pointer to **NullableFloat32** | ranking score; &lt;0.5 often unreliable | [optional] 
**Locator** | Pointer to **map[string]interface{}** | structured unit key (pk/number/thread_ts) | [optional] 
**Metadata** | Pointer to **map[string]interface{}** | chunk_kind, connector_type, fields, ... | [optional] 

## Methods

### NewResultEnvelope

`func NewResultEnvelope(source string, ) *ResultEnvelope`

NewResultEnvelope instantiates a new ResultEnvelope object
This constructor will assign default values to properties that have it defined,
and makes sure properties required by API are set, but the set of arguments
will change when the set of required properties is changed

### NewResultEnvelopeWithDefaults

`func NewResultEnvelopeWithDefaults() *ResultEnvelope`

NewResultEnvelopeWithDefaults instantiates a new ResultEnvelope object
This constructor will only assign default values to properties that have it defined,
but it doesn't guarantee that properties required by API are set

### GetSource

`func (o *ResultEnvelope) GetSource() string`

GetSource returns the Source field if non-nil, zero value otherwise.

### GetSourceOk

`func (o *ResultEnvelope) GetSourceOk() (*string, bool)`

GetSourceOk returns a tuple with the Source field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetSource

`func (o *ResultEnvelope) SetSource(v string)`

SetSource sets Source field to given value.


### GetLines

`func (o *ResultEnvelope) GetLines() []int32`

GetLines returns the Lines field if non-nil, zero value otherwise.

### GetLinesOk

`func (o *ResultEnvelope) GetLinesOk() (*[]int32, bool)`

GetLinesOk returns a tuple with the Lines field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetLines

`func (o *ResultEnvelope) SetLines(v []int32)`

SetLines sets Lines field to given value.

### HasLines

`func (o *ResultEnvelope) HasLines() bool`

HasLines returns a boolean if a field has been set.

### SetLinesNil

`func (o *ResultEnvelope) SetLinesNil(b bool)`

 SetLinesNil sets the value for Lines to be an explicit nil

### UnsetLines
`func (o *ResultEnvelope) UnsetLines()`

UnsetLines ensures that no value is present for Lines, not even an explicit nil
### GetContent

`func (o *ResultEnvelope) GetContent() string`

GetContent returns the Content field if non-nil, zero value otherwise.

### GetContentOk

`func (o *ResultEnvelope) GetContentOk() (*string, bool)`

GetContentOk returns a tuple with the Content field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetContent

`func (o *ResultEnvelope) SetContent(v string)`

SetContent sets Content field to given value.

### HasContent

`func (o *ResultEnvelope) HasContent() bool`

HasContent returns a boolean if a field has been set.

### GetScore

`func (o *ResultEnvelope) GetScore() float32`

GetScore returns the Score field if non-nil, zero value otherwise.

### GetScoreOk

`func (o *ResultEnvelope) GetScoreOk() (*float32, bool)`

GetScoreOk returns a tuple with the Score field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetScore

`func (o *ResultEnvelope) SetScore(v float32)`

SetScore sets Score field to given value.

### HasScore

`func (o *ResultEnvelope) HasScore() bool`

HasScore returns a boolean if a field has been set.

### SetScoreNil

`func (o *ResultEnvelope) SetScoreNil(b bool)`

 SetScoreNil sets the value for Score to be an explicit nil

### UnsetScore
`func (o *ResultEnvelope) UnsetScore()`

UnsetScore ensures that no value is present for Score, not even an explicit nil
### GetLocator

`func (o *ResultEnvelope) GetLocator() map[string]interface{}`

GetLocator returns the Locator field if non-nil, zero value otherwise.

### GetLocatorOk

`func (o *ResultEnvelope) GetLocatorOk() (*map[string]interface{}, bool)`

GetLocatorOk returns a tuple with the Locator field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetLocator

`func (o *ResultEnvelope) SetLocator(v map[string]interface{})`

SetLocator sets Locator field to given value.

### HasLocator

`func (o *ResultEnvelope) HasLocator() bool`

HasLocator returns a boolean if a field has been set.

### SetLocatorNil

`func (o *ResultEnvelope) SetLocatorNil(b bool)`

 SetLocatorNil sets the value for Locator to be an explicit nil

### UnsetLocator
`func (o *ResultEnvelope) UnsetLocator()`

UnsetLocator ensures that no value is present for Locator, not even an explicit nil
### GetMetadata

`func (o *ResultEnvelope) GetMetadata() map[string]interface{}`

GetMetadata returns the Metadata field if non-nil, zero value otherwise.

### GetMetadataOk

`func (o *ResultEnvelope) GetMetadataOk() (*map[string]interface{}, bool)`

GetMetadataOk returns a tuple with the Metadata field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetMetadata

`func (o *ResultEnvelope) SetMetadata(v map[string]interface{})`

SetMetadata sets Metadata field to given value.

### HasMetadata

`func (o *ResultEnvelope) HasMetadata() bool`

HasMetadata returns a boolean if a field has been set.


[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


