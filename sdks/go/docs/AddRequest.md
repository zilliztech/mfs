# AddRequest

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**Target** | **string** | path or connector URI to register + index | 
**Full** | Pointer to **bool** | force full re-index (ignore caches/fingerprints) | [optional] [default to false]
**Since** | Pointer to **NullableString** | only index changes since this cursor/date | [optional] 
**Process** | Pointer to **bool** | True: index inline now; False: enqueue for a worker | [optional] [default to true]

## Methods

### NewAddRequest

`func NewAddRequest(target string, ) *AddRequest`

NewAddRequest instantiates a new AddRequest object
This constructor will assign default values to properties that have it defined,
and makes sure properties required by API are set, but the set of arguments
will change when the set of required properties is changed

### NewAddRequestWithDefaults

`func NewAddRequestWithDefaults() *AddRequest`

NewAddRequestWithDefaults instantiates a new AddRequest object
This constructor will only assign default values to properties that have it defined,
but it doesn't guarantee that properties required by API are set

### GetTarget

`func (o *AddRequest) GetTarget() string`

GetTarget returns the Target field if non-nil, zero value otherwise.

### GetTargetOk

`func (o *AddRequest) GetTargetOk() (*string, bool)`

GetTargetOk returns a tuple with the Target field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetTarget

`func (o *AddRequest) SetTarget(v string)`

SetTarget sets Target field to given value.


### GetFull

`func (o *AddRequest) GetFull() bool`

GetFull returns the Full field if non-nil, zero value otherwise.

### GetFullOk

`func (o *AddRequest) GetFullOk() (*bool, bool)`

GetFullOk returns a tuple with the Full field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetFull

`func (o *AddRequest) SetFull(v bool)`

SetFull sets Full field to given value.

### HasFull

`func (o *AddRequest) HasFull() bool`

HasFull returns a boolean if a field has been set.

### GetSince

`func (o *AddRequest) GetSince() string`

GetSince returns the Since field if non-nil, zero value otherwise.

### GetSinceOk

`func (o *AddRequest) GetSinceOk() (*string, bool)`

GetSinceOk returns a tuple with the Since field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetSince

`func (o *AddRequest) SetSince(v string)`

SetSince sets Since field to given value.

### HasSince

`func (o *AddRequest) HasSince() bool`

HasSince returns a boolean if a field has been set.

### SetSinceNil

`func (o *AddRequest) SetSinceNil(b bool)`

 SetSinceNil sets the value for Since to be an explicit nil

### UnsetSince
`func (o *AddRequest) UnsetSince()`

UnsetSince ensures that no value is present for Since, not even an explicit nil
### GetProcess

`func (o *AddRequest) GetProcess() bool`

GetProcess returns the Process field if non-nil, zero value otherwise.

### GetProcessOk

`func (o *AddRequest) GetProcessOk() (*bool, bool)`

GetProcessOk returns a tuple with the Process field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetProcess

`func (o *AddRequest) SetProcess(v bool)`

SetProcess sets Process field to given value.

### HasProcess

`func (o *AddRequest) HasProcess() bool`

HasProcess returns a boolean if a field has been set.


[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


