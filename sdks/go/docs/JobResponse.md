# JobResponse

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**Id** | **string** |  | 
**Status** | **string** |  | 
**OpKind** | Pointer to **NullableString** |  | [optional] 
**Trigger** | Pointer to **NullableString** |  | [optional] 
**Error** | Pointer to **NullableString** |  | [optional] 
**TotalObjects** | Pointer to **NullableInt32** |  | [optional] 
**SucceededObjects** | Pointer to **NullableInt32** |  | [optional] 
**FailedObjects** | Pointer to **NullableInt32** |  | [optional] 
**CancelledObjects** | Pointer to **NullableInt32** |  | [optional] 
**StartedAt** | Pointer to **NullableString** |  | [optional] 
**FinishedAt** | Pointer to **NullableString** |  | [optional] 

## Methods

### NewJobResponse

`func NewJobResponse(id string, status string, ) *JobResponse`

NewJobResponse instantiates a new JobResponse object
This constructor will assign default values to properties that have it defined,
and makes sure properties required by API are set, but the set of arguments
will change when the set of required properties is changed

### NewJobResponseWithDefaults

`func NewJobResponseWithDefaults() *JobResponse`

NewJobResponseWithDefaults instantiates a new JobResponse object
This constructor will only assign default values to properties that have it defined,
but it doesn't guarantee that properties required by API are set

### GetId

`func (o *JobResponse) GetId() string`

GetId returns the Id field if non-nil, zero value otherwise.

### GetIdOk

`func (o *JobResponse) GetIdOk() (*string, bool)`

GetIdOk returns a tuple with the Id field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetId

`func (o *JobResponse) SetId(v string)`

SetId sets Id field to given value.


### GetStatus

`func (o *JobResponse) GetStatus() string`

GetStatus returns the Status field if non-nil, zero value otherwise.

### GetStatusOk

`func (o *JobResponse) GetStatusOk() (*string, bool)`

GetStatusOk returns a tuple with the Status field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetStatus

`func (o *JobResponse) SetStatus(v string)`

SetStatus sets Status field to given value.


### GetOpKind

`func (o *JobResponse) GetOpKind() string`

GetOpKind returns the OpKind field if non-nil, zero value otherwise.

### GetOpKindOk

`func (o *JobResponse) GetOpKindOk() (*string, bool)`

GetOpKindOk returns a tuple with the OpKind field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetOpKind

`func (o *JobResponse) SetOpKind(v string)`

SetOpKind sets OpKind field to given value.

### HasOpKind

`func (o *JobResponse) HasOpKind() bool`

HasOpKind returns a boolean if a field has been set.

### SetOpKindNil

`func (o *JobResponse) SetOpKindNil(b bool)`

 SetOpKindNil sets the value for OpKind to be an explicit nil

### UnsetOpKind
`func (o *JobResponse) UnsetOpKind()`

UnsetOpKind ensures that no value is present for OpKind, not even an explicit nil
### GetTrigger

`func (o *JobResponse) GetTrigger() string`

GetTrigger returns the Trigger field if non-nil, zero value otherwise.

### GetTriggerOk

`func (o *JobResponse) GetTriggerOk() (*string, bool)`

GetTriggerOk returns a tuple with the Trigger field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetTrigger

`func (o *JobResponse) SetTrigger(v string)`

SetTrigger sets Trigger field to given value.

### HasTrigger

`func (o *JobResponse) HasTrigger() bool`

HasTrigger returns a boolean if a field has been set.

### SetTriggerNil

`func (o *JobResponse) SetTriggerNil(b bool)`

 SetTriggerNil sets the value for Trigger to be an explicit nil

### UnsetTrigger
`func (o *JobResponse) UnsetTrigger()`

UnsetTrigger ensures that no value is present for Trigger, not even an explicit nil
### GetError

`func (o *JobResponse) GetError() string`

GetError returns the Error field if non-nil, zero value otherwise.

### GetErrorOk

`func (o *JobResponse) GetErrorOk() (*string, bool)`

GetErrorOk returns a tuple with the Error field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetError

`func (o *JobResponse) SetError(v string)`

SetError sets Error field to given value.

### HasError

`func (o *JobResponse) HasError() bool`

HasError returns a boolean if a field has been set.

### SetErrorNil

`func (o *JobResponse) SetErrorNil(b bool)`

 SetErrorNil sets the value for Error to be an explicit nil

### UnsetError
`func (o *JobResponse) UnsetError()`

UnsetError ensures that no value is present for Error, not even an explicit nil
### GetTotalObjects

`func (o *JobResponse) GetTotalObjects() int32`

GetTotalObjects returns the TotalObjects field if non-nil, zero value otherwise.

### GetTotalObjectsOk

`func (o *JobResponse) GetTotalObjectsOk() (*int32, bool)`

GetTotalObjectsOk returns a tuple with the TotalObjects field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetTotalObjects

`func (o *JobResponse) SetTotalObjects(v int32)`

SetTotalObjects sets TotalObjects field to given value.

### HasTotalObjects

`func (o *JobResponse) HasTotalObjects() bool`

HasTotalObjects returns a boolean if a field has been set.

### SetTotalObjectsNil

`func (o *JobResponse) SetTotalObjectsNil(b bool)`

 SetTotalObjectsNil sets the value for TotalObjects to be an explicit nil

### UnsetTotalObjects
`func (o *JobResponse) UnsetTotalObjects()`

UnsetTotalObjects ensures that no value is present for TotalObjects, not even an explicit nil
### GetSucceededObjects

`func (o *JobResponse) GetSucceededObjects() int32`

GetSucceededObjects returns the SucceededObjects field if non-nil, zero value otherwise.

### GetSucceededObjectsOk

`func (o *JobResponse) GetSucceededObjectsOk() (*int32, bool)`

GetSucceededObjectsOk returns a tuple with the SucceededObjects field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetSucceededObjects

`func (o *JobResponse) SetSucceededObjects(v int32)`

SetSucceededObjects sets SucceededObjects field to given value.

### HasSucceededObjects

`func (o *JobResponse) HasSucceededObjects() bool`

HasSucceededObjects returns a boolean if a field has been set.

### SetSucceededObjectsNil

`func (o *JobResponse) SetSucceededObjectsNil(b bool)`

 SetSucceededObjectsNil sets the value for SucceededObjects to be an explicit nil

### UnsetSucceededObjects
`func (o *JobResponse) UnsetSucceededObjects()`

UnsetSucceededObjects ensures that no value is present for SucceededObjects, not even an explicit nil
### GetFailedObjects

`func (o *JobResponse) GetFailedObjects() int32`

GetFailedObjects returns the FailedObjects field if non-nil, zero value otherwise.

### GetFailedObjectsOk

`func (o *JobResponse) GetFailedObjectsOk() (*int32, bool)`

GetFailedObjectsOk returns a tuple with the FailedObjects field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetFailedObjects

`func (o *JobResponse) SetFailedObjects(v int32)`

SetFailedObjects sets FailedObjects field to given value.

### HasFailedObjects

`func (o *JobResponse) HasFailedObjects() bool`

HasFailedObjects returns a boolean if a field has been set.

### SetFailedObjectsNil

`func (o *JobResponse) SetFailedObjectsNil(b bool)`

 SetFailedObjectsNil sets the value for FailedObjects to be an explicit nil

### UnsetFailedObjects
`func (o *JobResponse) UnsetFailedObjects()`

UnsetFailedObjects ensures that no value is present for FailedObjects, not even an explicit nil
### GetCancelledObjects

`func (o *JobResponse) GetCancelledObjects() int32`

GetCancelledObjects returns the CancelledObjects field if non-nil, zero value otherwise.

### GetCancelledObjectsOk

`func (o *JobResponse) GetCancelledObjectsOk() (*int32, bool)`

GetCancelledObjectsOk returns a tuple with the CancelledObjects field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetCancelledObjects

`func (o *JobResponse) SetCancelledObjects(v int32)`

SetCancelledObjects sets CancelledObjects field to given value.

### HasCancelledObjects

`func (o *JobResponse) HasCancelledObjects() bool`

HasCancelledObjects returns a boolean if a field has been set.

### SetCancelledObjectsNil

`func (o *JobResponse) SetCancelledObjectsNil(b bool)`

 SetCancelledObjectsNil sets the value for CancelledObjects to be an explicit nil

### UnsetCancelledObjects
`func (o *JobResponse) UnsetCancelledObjects()`

UnsetCancelledObjects ensures that no value is present for CancelledObjects, not even an explicit nil
### GetStartedAt

`func (o *JobResponse) GetStartedAt() string`

GetStartedAt returns the StartedAt field if non-nil, zero value otherwise.

### GetStartedAtOk

`func (o *JobResponse) GetStartedAtOk() (*string, bool)`

GetStartedAtOk returns a tuple with the StartedAt field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetStartedAt

`func (o *JobResponse) SetStartedAt(v string)`

SetStartedAt sets StartedAt field to given value.

### HasStartedAt

`func (o *JobResponse) HasStartedAt() bool`

HasStartedAt returns a boolean if a field has been set.

### SetStartedAtNil

`func (o *JobResponse) SetStartedAtNil(b bool)`

 SetStartedAtNil sets the value for StartedAt to be an explicit nil

### UnsetStartedAt
`func (o *JobResponse) UnsetStartedAt()`

UnsetStartedAt ensures that no value is present for StartedAt, not even an explicit nil
### GetFinishedAt

`func (o *JobResponse) GetFinishedAt() string`

GetFinishedAt returns the FinishedAt field if non-nil, zero value otherwise.

### GetFinishedAtOk

`func (o *JobResponse) GetFinishedAtOk() (*string, bool)`

GetFinishedAtOk returns a tuple with the FinishedAt field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetFinishedAt

`func (o *JobResponse) SetFinishedAt(v string)`

SetFinishedAt sets FinishedAt field to given value.

### HasFinishedAt

`func (o *JobResponse) HasFinishedAt() bool`

HasFinishedAt returns a boolean if a field has been set.

### SetFinishedAtNil

`func (o *JobResponse) SetFinishedAtNil(b bool)`

 SetFinishedAtNil sets the value for FinishedAt to be an explicit nil

### UnsetFinishedAt
`func (o *JobResponse) UnsetFinishedAt()`

UnsetFinishedAt ensures that no value is present for FinishedAt, not even an explicit nil

[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


