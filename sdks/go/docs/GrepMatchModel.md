# GrepMatchModel

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**Source** | Pointer to **NullableString** |  | [optional] 
**Lines** | Pointer to **[]int32** |  | [optional] 
**Content** | Pointer to **string** |  | [optional] [default to ""]
**Via** | Pointer to **NullableString** | bm25 | linear | pushdown | [optional] 

## Methods

### NewGrepMatchModel

`func NewGrepMatchModel() *GrepMatchModel`

NewGrepMatchModel instantiates a new GrepMatchModel object
This constructor will assign default values to properties that have it defined,
and makes sure properties required by API are set, but the set of arguments
will change when the set of required properties is changed

### NewGrepMatchModelWithDefaults

`func NewGrepMatchModelWithDefaults() *GrepMatchModel`

NewGrepMatchModelWithDefaults instantiates a new GrepMatchModel object
This constructor will only assign default values to properties that have it defined,
but it doesn't guarantee that properties required by API are set

### GetSource

`func (o *GrepMatchModel) GetSource() string`

GetSource returns the Source field if non-nil, zero value otherwise.

### GetSourceOk

`func (o *GrepMatchModel) GetSourceOk() (*string, bool)`

GetSourceOk returns a tuple with the Source field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetSource

`func (o *GrepMatchModel) SetSource(v string)`

SetSource sets Source field to given value.

### HasSource

`func (o *GrepMatchModel) HasSource() bool`

HasSource returns a boolean if a field has been set.

### SetSourceNil

`func (o *GrepMatchModel) SetSourceNil(b bool)`

 SetSourceNil sets the value for Source to be an explicit nil

### UnsetSource
`func (o *GrepMatchModel) UnsetSource()`

UnsetSource ensures that no value is present for Source, not even an explicit nil
### GetLines

`func (o *GrepMatchModel) GetLines() []int32`

GetLines returns the Lines field if non-nil, zero value otherwise.

### GetLinesOk

`func (o *GrepMatchModel) GetLinesOk() (*[]int32, bool)`

GetLinesOk returns a tuple with the Lines field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetLines

`func (o *GrepMatchModel) SetLines(v []int32)`

SetLines sets Lines field to given value.

### HasLines

`func (o *GrepMatchModel) HasLines() bool`

HasLines returns a boolean if a field has been set.

### SetLinesNil

`func (o *GrepMatchModel) SetLinesNil(b bool)`

 SetLinesNil sets the value for Lines to be an explicit nil

### UnsetLines
`func (o *GrepMatchModel) UnsetLines()`

UnsetLines ensures that no value is present for Lines, not even an explicit nil
### GetContent

`func (o *GrepMatchModel) GetContent() string`

GetContent returns the Content field if non-nil, zero value otherwise.

### GetContentOk

`func (o *GrepMatchModel) GetContentOk() (*string, bool)`

GetContentOk returns a tuple with the Content field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetContent

`func (o *GrepMatchModel) SetContent(v string)`

SetContent sets Content field to given value.

### HasContent

`func (o *GrepMatchModel) HasContent() bool`

HasContent returns a boolean if a field has been set.

### GetVia

`func (o *GrepMatchModel) GetVia() string`

GetVia returns the Via field if non-nil, zero value otherwise.

### GetViaOk

`func (o *GrepMatchModel) GetViaOk() (*string, bool)`

GetViaOk returns a tuple with the Via field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetVia

`func (o *GrepMatchModel) SetVia(v string)`

SetVia sets Via field to given value.

### HasVia

`func (o *GrepMatchModel) HasVia() bool`

HasVia returns a boolean if a field has been set.

### SetViaNil

`func (o *GrepMatchModel) SetViaNil(b bool)`

 SetViaNil sets the value for Via to be an explicit nil

### UnsetVia
`func (o *GrepMatchModel) UnsetVia()`

UnsetVia ensures that no value is present for Via, not even an explicit nil

[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


