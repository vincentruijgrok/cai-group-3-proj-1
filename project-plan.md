# Project Plan: Collaborative AI - Trust Mechanism

**Group 3** Rembrandt Hazeleger, Wilhelm Marcu, Vincent Ruijgrok, Viktor Sersik

- [Project Plan: Collaborative AI - Trust Mechanism](#project-plan-collaborative-ai---trust-mechanism)
  - [1. Project Overview](#1-project-overview)
  - [2. Team Members and Responsibilities](#2-team-members-and-responsibilities)
  - [3. Approach to Trust Mechanism](#3-approach-to-trust-mechanism)
  - [4. Evaluation Plan](#4-evaluation-plan)
  - [5. Workflow and Collaboration](#5-workflow-and-collaboration)
  - [6. Meetings and Checkpoints](#6-meetings-and-checkpoints)
  - [7. Timeline](#7-timeline)

## 1. Project Overview
This project focuses on implementing a trust mechanism for an artificial agent (RescueBot) in a search and rescue environment using the MATRX framework. The agent must adapt its behavior based on trust beliefs formed during interactions with human teammates. The project will be developed in Python 3.8/3.9 using the provided starter code repository.

## 2. Team Members and Responsibilities

- **Rembrandt & Wilhelm** - Implementation and Behavior Adaptation
  - Develop the core trust mechanism, including trust beliefs (competence and willingness) and updates based on direct experience.
  - Modify RescueBot's decision-making to adjust behavior based on trust beliefs.
  - Implement different adaptation strategies and evaluate their effectiveness.
  - Ensure the agent can load and save trust values using a memory file.

- **Vincent & Viktor** - Evaluation and Documentation
  - Design and conduct experiments comparing the trust mechanism against the three baselines (NEVER-TRUST, ALWAYS-TRUST, RANDOM-TRUST).
  - Collect performance metrics such as mission completion time and agent/human actions.
  - Generate plots showing performance trends across different teammate behaviors.
  - Maintain project documentation, ensuring clarity in implementation details.
  - Manage code integration and submission, ensuring the repository follows the provided structure.

## 3. Approach to Trust Mechanism
- Define trust beliefs for at least two tasks: **search** and **rescue**, probably split search into **search rooms** and **destroy obstacles**.
- Implement a belief update mechanism that adjusts trust values based on observed teammate behavior.
- Use a memory file to store and retrieve past trust values.
- Ensure the agent can handle teammates with varying characteristics (e.g., honest, liar, lazy).
- Integrate decision-making logic so the agent adapts based on trust beliefs.

## 4. Evaluation Plan
- Compare the implemented trust mechanism against the three baselines.
- Measure:
  - **Mission completion time**
  - **Number of agent and human actions**
  - **Trust value changes over multiple rounds**
- Generate plots showing performance trends across different teammate behaviors.

## 5. Workflow and Collaboration
- Use a **GitHub repository** for version control.
- Work on feature branches before merging to the main branch.
- Ensure code is well-documented and follows the provided project structure.

## 6. Meetings and Checkpoints
- Meet during labs to discuss progress and address challenges.
- Final integration and testing before submission.

## 7. Timeline
| Date | Milestone |
|------|-----------|
| Feb 21, 2025 | Submit project plan |
| Feb 28, 2025 | Complete initial trust mechanism implementation |
| Mar 4, 2025 | Finalize behavior adaptation and evaluation |
| Mar 7, 2025 | Submit implementation and group report |
