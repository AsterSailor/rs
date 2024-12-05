import { createApi } from "@reduxjs/toolkit/query/react";
import { assignmentApi } from "@store/assignment/assignment.logic.api";
import { baseQuery } from "@store/baseQuery";
import toast from "react-hot-toast";

import { RootState } from "@/state/store";
import { DetailResponse } from "@/types/api";
import { CreateExercisesPayload } from "@/types/exercises";

export const exercisesApi = createApi({
  reducerPath: "exercisesAPI",
  keepUnusedDataFor: 60, //change to 0 for tests,
  baseQuery: baseQuery,
  tagTypes: ["Exercises"],
  endpoints: (build) => ({
    createNewExercise: build.mutation<number, CreateExercisesPayload>({
      query: (body) => ({
        method: "POST",
        url: "/assignment/instructor/new_question",
        body
      }),
      transformResponse: (response: DetailResponse<{ id: number }>) => {
        return response.detail.id;
      },
      onQueryStarted: (createExercisePayload, { queryFulfilled, dispatch, getState }) => {
        queryFulfilled
          .then((response) => {
            const state = getState() as RootState;

            dispatch(
              assignmentApi.endpoints.createAssignmentExercise.initiate({
                assignment_id: state.assignmentTemp.selectedAssignment?.id!,
                autograde: null,
                id: response.data,
                points: createExercisePayload.points,
                qnumber: createExercisePayload.name,
                question_id: response.data,
                which_to_grade: "best_answer"
              })
            );
          })
          .catch(() => {
            toast("Error creating new exercise", {
              icon: "🔥"
            });
          });
      }
    })
  })
});

export const { useCreateNewExerciseMutation } = exercisesApi;
