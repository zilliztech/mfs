# CancelResponse

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**JobId** | **string** |  | 
**Cancelled** | **bool** |  | 

## Methods

### NewCancelResponse

`func NewCancelResponse(jobId string, cancelled bool, ) *CancelResponse`

NewCancelResponse instantiates a new CancelResponse object
This constructor will assign default values to properties that have it defined,
and makes sure properties required by API are set, but the set of arguments
will change when the set of required properties is changed

### NewCancelResponseWithDefaults

`func NewCancelResponseWithDefaults() *CancelResponse`

NewCancelResponseWithDefaults instantiates a new CancelResponse object
This constructor will only assign default values to properties that have it defined,
but it doesn't guarantee that properties required by API are set

### GetJobId

`func (o *CancelResponse) GetJobId() string`

GetJobId returns the JobId field if non-nil, zero value otherwise.

### GetJobIdOk

`func (o *CancelResponse) GetJobIdOk() (*string, bool)`

GetJobIdOk returns a tuple with the JobId field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetJobId

`func (o *CancelResponse) SetJobId(v string)`

SetJobId sets JobId field to given value.


### GetCancelled

`func (o *CancelResponse) GetCancelled() bool`

GetCancelled returns the Cancelled field if non-nil, zero value otherwise.

### GetCancelledOk

`func (o *CancelResponse) GetCancelledOk() (*bool, bool)`

GetCancelledOk returns a tuple with the Cancelled field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetCancelled

`func (o *CancelResponse) SetCancelled(v bool)`

SetCancelled sets Cancelled field to given value.



[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


